import argparse
import os
import re
import shutil
import subprocess
import tempfile
from PIL import Image, ImageFilter

OPACITY = 0.35  # 0.0 = invisible, 1.0 = fully opaque
# Watermark size as a fraction of the frame's SHORTER side (adapts to any ratio)
BASE_SCALE = 0.42
SCALE_BY_ORIENTATION = {
    'portrait': 0.40,
    'square': 0.42,
    'landscape': 0.42,
    'ultrawide': 0.48,
}
MIN_WATERMARK_PX = 80
MAX_WATERMARK_PX = 900
BLACK_THRESHOLD = 30
OUTLINE_SIZE = 2

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff')
VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.webm', '.mkv', '.m4v', '.wmv')


def resolve_path_input(raw, cwd=None):
    """Resolve a user path: absolute as-is, otherwise relative to cwd."""
    cwd = cwd or os.getcwd()
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return None
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(cwd, raw))


def ask_watermark_path():
    """
    Ask for the watermark image: full path, or filename relative to cwd.
    Press Enter to use watermark_center.png if it exists.
    """
    cwd = os.getcwd()
    default_candidates = [
        os.path.join(cwd, 'watermark_center.png'),
        os.path.join(os.path.dirname(__file__), 'watermark_center.png'),
    ]
    default_path = next((p for p in default_candidates if os.path.isfile(p)), None)

    print(f"Current directory: {cwd}")
    print("Enter the watermark image:")
    print("  - Full path, e.g. E:\\AstroLinx\\watermarked_assets\\watermark_center.png")
    print("  - Or filename only, e.g. watermark_center.png  (resolved from current directory)")
    if default_path:
        print(f"  - Press Enter to use default: {default_path}")

    raw = input("Watermark image: ").strip().strip('"').strip("'")
    if not raw:
        if default_path:
            return default_path
        raise SystemExit("No watermark image specified. Exiting.")

    path = resolve_path_input(raw, cwd)
    if not os.path.isfile(path):
        raise SystemExit(f"Watermark image not found: {path}")

    if not path.lower().endswith(IMAGE_EXTENSIONS):
        raise SystemExit(
            f"Unsupported watermark type: {path}\n"
            f"Use one of: {', '.join(IMAGE_EXTENSIONS)}"
        )

    return path


def prepare_watermark(watermark_path):
    """Load watermark, remove black bg, convert figure to white + dark outline."""
    watermark_raw = Image.open(watermark_path).convert('RGBA')
    pixels = watermark_raw.load()
    width, height = watermark_raw.size

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if r <= BLACK_THRESHOLD and g <= BLACK_THRESHOLD and b <= BLACK_THRESHOLD:
                pixels[x, y] = (0, 0, 0, 0)
            else:
                pixels[x, y] = (255, 255, 255, 255)

    bbox = watermark_raw.getbbox()
    if bbox:
        watermark_raw = watermark_raw.crop(bbox)

    alpha = watermark_raw.split()[3]
    outline = Image.new('RGBA', watermark_raw.size, (0, 0, 0, 0))
    outline_mask = alpha.filter(ImageFilter.MaxFilter(OUTLINE_SIZE * 2 + 1))
    outline.paste((0, 0, 0, 180), mask=outline_mask)
    outline.paste(watermark_raw, mask=alpha)
    return outline


def apply_opacity(image, opacity):
    r, g, b, a = image.split()
    a = a.point(lambda p: int(p * opacity))
    return Image.merge('RGBA', (r, g, b, a))


def analyze_frame(width, height):
    """
    Inspect size and aspect ratio, then recommend a centered watermark size.
    Scaling uses the shorter side so landscape, portrait, and ultrawide
    frames all get a proportionally consistent logo.
    """
    ratio = width / height if height else 1.0

    if ratio >= 1.8:
        orientation = 'ultrawide'
    elif ratio > 1.05:
        orientation = 'landscape'
    elif ratio < 0.95:
        orientation = 'portrait'
    else:
        orientation = 'square'

    short_side = min(width, height)
    long_side = max(width, height)
    scale = SCALE_BY_ORIENTATION.get(orientation, BASE_SCALE)

    target_w = int(short_side * scale)
    target_w = max(MIN_WATERMARK_PX, min(MAX_WATERMARK_PX, target_w))

    return {
        'width': width,
        'height': height,
        'ratio': ratio,
        'orientation': orientation,
        'short_side': short_side,
        'long_side': long_side,
        'scale': scale,
        'target_w': target_w,
    }


def fit_watermark_size(info, frame_w, frame_h, wm_aspect):
    """Clamp watermark so it stays inside the frame."""
    target_w = info['target_w']
    target_h = max(1, int(target_w * wm_aspect))

    if target_h > frame_h * 0.85:
        target_h = max(1, int(frame_h * 0.85))
        target_w = max(1, int(target_h / wm_aspect))
    if target_w > frame_w * 0.85:
        target_w = max(1, int(frame_w * 0.85))
        target_h = max(1, int(target_w * wm_aspect))

    return target_w, target_h


def get_ffmpeg_exe():
    """Prefer system ffmpeg; fall back to imageio-ffmpeg bundled binary."""
    system = shutil.which('ffmpeg')
    if system:
        return system
    try:
        from imageio_ffmpeg import get_ffmpeg_exe as bundled_ffmpeg
        return bundled_ffmpeg()
    except Exception as e:
        raise RuntimeError(
            "FFmpeg not found. Install system FFmpeg or: pip install imageio-ffmpeg"
        ) from e


def get_video_size(video_path, ffmpeg_exe):
    """Read video width/height via ffmpeg -i stderr (no ffprobe required)."""
    result = subprocess.run(
        [ffmpeg_exe, '-i', video_path],
        capture_output=True,
        text=True,
    )
    # Stream info is written to stderr even though exit code is non-zero
    stderr = result.stderr or ''
    match = re.search(r'Stream #.*Video:.*?(\d{2,5})x(\d{2,5})', stderr)
    if not match:
        raise ValueError(f"Could not read video dimensions: {video_path}")
    return int(match.group(1)), int(match.group(2))


def collect_media(target_dirs):
    """Walk folders recursively; collect images and videos separately."""
    images = []
    videos = []
    other_skipped = []

    for target_dir in target_dirs:
        if not os.path.exists(target_dir):
            print(f"Directory not found: {target_dir}")
            continue
        for root, _, files in os.walk(target_dir):
            for file in files:
                full_path = os.path.join(root, file)
                lower = file.lower()
                if lower.endswith(IMAGE_EXTENSIONS):
                    images.append(full_path)
                elif lower.endswith(VIDEO_EXTENSIONS):
                    videos.append(full_path)
                else:
                    other_skipped.append(full_path)

    return images, videos, other_skipped


def _subfolder_label(path, target_root):
    if target_root:
        try:
            rel = os.path.relpath(path, target_root)
            parts = rel.split(os.sep)
            return parts[0] if len(parts) > 1 else '(root)'
        except ValueError:
            return '(other)'
    parts = os.path.normpath(path).split(os.sep)
    return parts[1] if len(parts) > 2 else '(root)'


def print_folder_analysis(image_paths, video_paths=None, target_dirs=None, target_root=None, ffmpeg_exe=None):
    """Scan images (and videos if FFmpeg available) and print size/ratio summary."""
    label = ', '.join(target_dirs) if target_dirs else 'folder'
    video_paths = video_paths or []
    target_root = os.path.normpath(target_root) if target_root else None

    analyses = []
    orientations = {'portrait': 0, 'square': 0, 'landscape': 0, 'ultrawide': 0}
    subfolder_counts = {}
    video_analyses = []

    for path in image_paths:
        with Image.open(path) as im:
            info = analyze_frame(im.width, im.height)
            info['path'] = path
            info['kind'] = 'image'
            analyses.append(info)
            orientations[info['orientation']] += 1
        sub = _subfolder_label(path, target_root)
        subfolder_counts[sub] = subfolder_counts.get(sub, 0) + 1

    if video_paths and ffmpeg_exe:
        for path in video_paths:
            try:
                w, h = get_video_size(path, ffmpeg_exe)
                info = analyze_frame(w, h)
                info['path'] = path
                info['kind'] = 'video'
                video_analyses.append(info)
                orientations[info['orientation']] += 1
                sub = _subfolder_label(path, target_root)
                subfolder_counts[sub] = subfolder_counts.get(sub, 0) + 1
            except Exception as e:
                print(f"Warning: could not analyze video {path}: {e}")

    print(f"\n=== {label} folder analysis ===")
    print(f"Images to watermark: {len(image_paths)}")
    print(f"Videos to watermark: {len(video_paths)} (audio will be removed)")
    print(f"Subfolders touched:  {len(subfolder_counts)}")
    for name in sorted(subfolder_counts):
        print(f"  - {name}: {subfolder_counts[name]} media file(s)")

    all_info = analyses + video_analyses
    if all_info:
        widths = [a['width'] for a in all_info]
        heights = [a['height'] for a in all_info]
        ratios = [a['ratio'] for a in all_info]
        print(f"Width range:  {min(widths)} – {max(widths)} px")
        print(f"Height range: {min(heights)} – {max(heights)} px")
        print(f"Ratio range:  {min(ratios):.2f} – {max(ratios):.2f}")
        print(
            "Orientations: "
            f"portrait={orientations['portrait']}, "
            f"square={orientations['square']}, "
            f"landscape={orientations['landscape']}, "
            f"ultrawide={orientations['ultrawide']}"
        )
        print(
            f"Watermark sizing: {BASE_SCALE:.0%} of short side "
            f"(clamped {MIN_WATERMARK_PX}–{MAX_WATERMARK_PX}px), "
            f"tweaked per orientation"
        )
    print("================================\n")

    by_path = {a['path']: a for a in all_info}
    return by_path


def add_watermark_to_image(image_path, watermark_src, analysis=None):
    try:
        with Image.open(image_path) as base_image:
            orig_mode = base_image.mode
            info = analysis or analyze_frame(base_image.width, base_image.height)

            wm_aspect = watermark_src.height / watermark_src.width
            target_w, target_h = fit_watermark_size(
                info, base_image.width, base_image.height, wm_aspect
            )

            resample_filter = getattr(Image.Resampling, 'LANCZOS', getattr(Image, 'ANTIALIAS', 1))
            watermark = watermark_src.resize((target_w, target_h), resample_filter)
            watermark = apply_opacity(watermark.convert('RGBA'), OPACITY)

            pos_x = (base_image.width - watermark.width) // 2
            pos_y = (base_image.height - watermark.height) // 2

            base_rgba = base_image.convert('RGBA')
            base_rgba.paste(watermark, (pos_x, pos_y), mask=watermark)

            if image_path.lower().endswith(('.jpg', '.jpeg')) or orig_mode == 'RGB':
                final_image = base_rgba.convert('RGB')
                final_image.save(image_path, quality=95)
            else:
                base_rgba.save(image_path)

            print(
                f"Watermarked image: {image_path}\n"
                f"  size={info['width']}x{info['height']}  "
                f"ratio={info['ratio']:.2f}  "
                f"orientation={info['orientation']}\n"
                f"  watermark={target_w}x{target_h}  "
                f"scale={info['scale']:.0%} of short side ({info['short_side']}px)  "
                f"opacity={OPACITY}"
            )
    except Exception as e:
        print(f"Error processing image {image_path}: {e}")


def add_watermark_to_video(video_path, watermark_src, ffmpeg_exe, analysis=None):
    """Center-overlay logo (ratio-aware), re-encode video, strip all audio."""
    temp_wm = None
    temp_out = None
    try:
        width, height = get_video_size(video_path, ffmpeg_exe)
        info = analysis or analyze_frame(width, height)

        wm_aspect = watermark_src.height / watermark_src.width
        target_w, target_h = fit_watermark_size(info, width, height, wm_aspect)

        resample_filter = getattr(Image.Resampling, 'LANCZOS', getattr(Image, 'ANTIALIAS', 1))
        watermark = watermark_src.resize((target_w, target_h), resample_filter)
        watermark = apply_opacity(watermark.convert('RGBA'), OPACITY)

        # Keep temps on the same drive/folder as the video (Windows can't os.replace across drives)
        video_dir = os.path.dirname(os.path.abspath(video_path)) or '.'

        fd, temp_wm = tempfile.mkstemp(suffix='.png', dir=video_dir)
        os.close(fd)
        watermark.save(temp_wm, 'PNG')

        ext = os.path.splitext(video_path)[1] or '.mp4'
        fd, temp_out = tempfile.mkstemp(suffix=ext, prefix='.wm_tmp_', dir=video_dir)
        os.close(fd)
        os.unlink(temp_out)  # ffmpeg needs a free path

        # Overlay centered; -an removes audio completely
        cmd = [
            ffmpeg_exe,
            '-y',
            '-i', video_path,
            '-i', temp_wm,
            '-filter_complex',
            '[1:v]format=rgba[wm];[0:v][wm]overlay=(W-w)/2:(H-h)/2',
            '-an',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            temp_out,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(temp_out):
            err = (result.stderr or result.stdout or '').strip().splitlines()
            tail = '\n'.join(err[-8:]) if err else 'unknown ffmpeg error'
            raise RuntimeError(tail)

        os.replace(temp_out, video_path)
        temp_out = None

        print(
            f"Watermarked video (muted): {video_path}\n"
            f"  size={info['width']}x{info['height']}  "
            f"ratio={info['ratio']:.2f}  "
            f"orientation={info['orientation']}\n"
            f"  watermark={target_w}x{target_h}  "
            f"scale={info['scale']:.0%} of short side ({info['short_side']}px)  "
            f"opacity={OPACITY}"
        )
        return True
    except Exception as e:
        print(f"Error processing video {video_path}: {e}")
        return False
    finally:
        if temp_wm and os.path.exists(temp_wm):
            os.unlink(temp_wm)
        if temp_out and os.path.exists(temp_out):
            os.unlink(temp_out)


def resolve_target_folder(test_mode=False):
    """
    Resolve target folder.
    --test: use ./test under the current working directory (no prompt).
    Otherwise ask for a full path or folder name relative to cwd.
    """
    cwd = os.getcwd()

    if test_mode:
        folder = os.path.normpath(os.path.join(cwd, 'test'))
        if not os.path.isdir(folder):
            raise SystemExit(f"Test folder not found: {folder}")
        return folder

    print("Enter the target folder:")
    print("  - Full path, e.g. E:\\AstroLinx\\watermarked_assets\\works")
    print("  - Or folder name only, e.g. works  (resolved from current directory)")

    raw = input("Folder: ").strip().strip('"').strip("'")
    if not raw:
        raise SystemExit("No folder specified. Exiting.")

    folder = resolve_path_input(raw, cwd)
    if not os.path.isdir(folder):
        raise SystemExit(f"Folder not found: {folder}")

    return folder


def parse_args():
    parser = argparse.ArgumentParser(
        description='Center-watermark images/videos with adaptive sizing.'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Only process images in the local ./test folder (skips videos and folder prompt).',
    )
    return parser.parse_args()


def main():
    args = parse_args()

    watermark_path = ask_watermark_path()
    print(f"Using watermark from: {watermark_path}")
    watermark_src = prepare_watermark(watermark_path)

    ffmpeg_exe = None
    if not args.test:
        try:
            ffmpeg_exe = get_ffmpeg_exe()
            print(f"Using FFmpeg: {ffmpeg_exe}")
        except RuntimeError as e:
            print(f"Warning: {e}")
            print("Videos will be skipped until FFmpeg / imageio-ffmpeg is available.")

    target_folder = resolve_target_folder(test_mode=args.test)
    if args.test:
        print(f"Test mode: processing images only in {target_folder}")
    else:
        print(f"Processing folder: {target_folder}")

    image_paths, video_paths, _other = collect_media([target_folder])
    if args.test:
        video_paths = []  # test mode: images only

    analysis_by_path = print_folder_analysis(
        image_paths,
        video_paths=video_paths,
        target_dirs=[os.path.basename(target_folder) or target_folder],
        target_root=target_folder,
        ffmpeg_exe=ffmpeg_exe,
    )

    if not image_paths and not video_paths:
        print("Nothing to watermark.")
        return

    image_count = 0
    for path in image_paths:
        add_watermark_to_image(path, watermark_src, analysis_by_path.get(path))
        image_count += 1

    video_count = 0
    video_failed = 0
    if video_paths:
        if not ffmpeg_exe:
            print(f"Skipping {len(video_paths)} video(s) — FFmpeg not available.")
            video_failed = len(video_paths)
        else:
            for path in video_paths:
                ok = add_watermark_to_video(
                    path, watermark_src, ffmpeg_exe, analysis_by_path.get(path)
                )
                if ok:
                    video_count += 1
                else:
                    video_failed += 1

    print(
        f"\nDone in {target_folder}: "
        f"{image_count} image(s), {video_count} video(s) watermarked (audio removed)"
        + (f", {video_failed} video(s) failed/skipped" if video_failed else "")
        + "."
    )


if __name__ == '__main__':
    main()
