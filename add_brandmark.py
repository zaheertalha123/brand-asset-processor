import argparse
import os
import re
import shutil
import subprocess
import tempfile
from PIL import Image

# Corner brand-mark settings (aligned with add_watermark.py)
BRAND_WIDTH_SCALE = 0.16  # brand width as fraction of frame width
MARGIN = 35
MIN_BRAND_PX = 40
MAX_BRAND_PX = 600

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff')
VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.webm', '.mkv', '.m4v', '.wmv')

POSITIONS = {
    1: 'upper_left',
    2: 'upper_right',
    3: 'lower_left',
    4: 'lower_right',
}
POSITION_LABELS = {
    'upper_left': 'Upper Left',
    'upper_right': 'Upper Right',
    'lower_left': 'Lower Left',
    'lower_right': 'Lower Right',
}


def resolve_path_input(raw, cwd=None):
    """Resolve a user path: absolute as-is, otherwise relative to cwd."""
    cwd = cwd or os.getcwd()
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return None
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(cwd, raw))


def ask_brandmark_path():
    """Ask for the brand-mark image (full path or filename relative to cwd)."""
    cwd = os.getcwd()
    default_candidates = [
        os.path.join(cwd, 'watermark.png'),
        os.path.join(os.path.dirname(__file__), 'watermark.png'),
    ]
    default_path = next((p for p in default_candidates if os.path.isfile(p)), None)

    print(f"Current directory: {cwd}")
    print("Enter the brand-mark image:")
    print("  - Full path, e.g. E:\\AstroLinx\\watermarked_assets\\watermark.png")
    print("  - Or filename only, e.g. watermark.png  (resolved from current directory)")
    if default_path:
        print(f"  - Press Enter to use default: {default_path}")

    raw = input("Brand-mark image: ").strip().strip('"').strip("'")
    if not raw:
        if default_path:
            return default_path
        raise SystemExit("No brand-mark image specified. Exiting.")

    path = resolve_path_input(raw, cwd)
    if not os.path.isfile(path):
        raise SystemExit(f"Brand-mark image not found: {path}")

    if not path.lower().endswith(IMAGE_EXTENSIONS):
        raise SystemExit(
            f"Unsupported brand-mark type: {path}\n"
            f"Use one of: {', '.join(IMAGE_EXTENSIONS)}"
        )

    return path


def ask_position():
    """Ask where to place the brand mark (numeric choice)."""
    print("\nWhere should the brand mark be placed?")
    print("  1) Upper Left")
    print("  2) Upper Right")
    print("  3) Lower Left")
    print("  4) Lower Right")
    print("  Press Enter for default: 2) Upper Right")

    while True:
        raw = input("Position [1-4]: ").strip()
        if not raw:
            return 'upper_right'
        if raw.isdigit() and int(raw) in POSITIONS:
            return POSITIONS[int(raw)]
        print("Please enter 1, 2, 3, or 4.")


def prepare_brandmark(brand_path):
    """Load brand mark and crop empty transparent margins."""
    brand_raw = Image.open(brand_path).convert('RGBA')
    bbox = brand_raw.getbbox()
    if bbox:
        return brand_raw.crop(bbox)
    return brand_raw


def analyze_frame(width, height):
    ratio = width / height if height else 1.0
    if ratio >= 1.8:
        orientation = 'ultrawide'
    elif ratio > 1.05:
        orientation = 'landscape'
    elif ratio < 0.95:
        orientation = 'portrait'
    else:
        orientation = 'square'

    target_w = int(width * BRAND_WIDTH_SCALE)
    target_w = max(MIN_BRAND_PX, min(MAX_BRAND_PX, target_w))

    # Keep margin usable on small frames
    margin = min(MARGIN, max(8, min(width, height) // 40))

    return {
        'width': width,
        'height': height,
        'ratio': ratio,
        'orientation': orientation,
        'target_w': target_w,
        'margin': margin,
    }


def brand_size(info, brand_src, frame_w, frame_h):
    wm_aspect = brand_src.height / brand_src.width
    target_w = info['target_w']
    target_h = max(1, int(target_w * wm_aspect))
    margin = info['margin']

    max_w = max(1, frame_w - 2 * margin)
    max_h = max(1, frame_h - 2 * margin)
    if target_w > max_w:
        target_w = max_w
        target_h = max(1, int(target_w * wm_aspect))
    if target_h > max_h:
        target_h = max_h
        target_w = max(1, int(target_h / wm_aspect))

    return target_w, target_h, margin


def corner_position(position, frame_w, frame_h, brand_w, brand_h, margin):
    if position == 'upper_left':
        return margin, margin
    if position == 'upper_right':
        return frame_w - brand_w - margin, margin
    if position == 'lower_left':
        return margin, frame_h - brand_h - margin
    if position == 'lower_right':
        return frame_w - brand_w - margin, frame_h - brand_h - margin
    raise ValueError(f"Unknown position: {position}")


def ffmpeg_overlay_expr(position, margin):
    """FFmpeg overlay x/y expressions for a corner placement."""
    m = int(margin)
    if position == 'upper_left':
        return f'{m}:{m}'
    if position == 'upper_right':
        return f'W-w-{m}:{m}'
    if position == 'lower_left':
        return f'{m}:H-h-{m}'
    if position == 'lower_right':
        return f'W-w-{m}:H-h-{m}'
    raise ValueError(f"Unknown position: {position}")


def get_ffmpeg_exe():
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
    result = subprocess.run(
        [ffmpeg_exe, '-i', video_path],
        capture_output=True,
        text=True,
    )
    stderr = result.stderr or ''
    match = re.search(r'Stream #.*Video:.*?(\d{2,5})x(\d{2,5})', stderr)
    if not match:
        raise ValueError(f"Could not read video dimensions: {video_path}")
    return int(match.group(1)), int(match.group(2))


def collect_media(target_dirs):
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
                video_analyses.append(info)
                orientations[info['orientation']] += 1
                sub = _subfolder_label(path, target_root)
                subfolder_counts[sub] = subfolder_counts.get(sub, 0) + 1
            except Exception as e:
                print(f"Warning: could not analyze video {path}: {e}")

    print(f"\n=== {label} folder analysis ===")
    print(f"Images to brand-mark: {len(image_paths)}")
    print(f"Videos to brand-mark: {len(video_paths)} (audio will be removed)")
    print(f"Subfolders touched:   {len(subfolder_counts)}")
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
        print(f"Brand size: ~{BRAND_WIDTH_SCALE:.0%} of frame width (clamped {MIN_BRAND_PX}–{MAX_BRAND_PX}px)")
    print("================================\n")

    return {a['path']: a for a in all_info}


def add_brandmark_to_image(image_path, brand_src, position, analysis=None):
    try:
        with Image.open(image_path) as base_image:
            orig_mode = base_image.mode
            info = analysis or analyze_frame(base_image.width, base_image.height)
            target_w, target_h, margin = brand_size(
                info, brand_src, base_image.width, base_image.height
            )

            resample_filter = getattr(Image.Resampling, 'LANCZOS', getattr(Image, 'ANTIALIAS', 1))
            brand = brand_src.resize((target_w, target_h), resample_filter).convert('RGBA')

            pos_x, pos_y = corner_position(
                position, base_image.width, base_image.height, brand.width, brand.height, margin
            )

            base_rgba = base_image.convert('RGBA')
            base_rgba.paste(brand, (pos_x, pos_y), mask=brand)

            if image_path.lower().endswith(('.jpg', '.jpeg')) or orig_mode == 'RGB':
                final_image = base_rgba.convert('RGB')
                final_image.save(image_path, quality=95)
            else:
                base_rgba.save(image_path)

            print(
                f"Brand-marked image: {image_path}\n"
                f"  size={info['width']}x{info['height']}  "
                f"ratio={info['ratio']:.2f}  "
                f"orientation={info['orientation']}\n"
                f"  brand={target_w}x{target_h}  "
                f"position={POSITION_LABELS[position]}  "
                f"margin={margin}px"
            )
    except Exception as e:
        print(f"Error processing image {image_path}: {e}")


def add_brandmark_to_video(video_path, brand_src, position, ffmpeg_exe, analysis=None):
    """Corner-overlay brand mark, re-encode video, strip all audio."""
    temp_wm = None
    temp_out = None
    try:
        width, height = get_video_size(video_path, ffmpeg_exe)
        info = analysis or analyze_frame(width, height)
        target_w, target_h, margin = brand_size(info, brand_src, width, height)

        resample_filter = getattr(Image.Resampling, 'LANCZOS', getattr(Image, 'ANTIALIAS', 1))
        brand = brand_src.resize((target_w, target_h), resample_filter).convert('RGBA')

        video_dir = os.path.dirname(os.path.abspath(video_path)) or '.'

        fd, temp_wm = tempfile.mkstemp(suffix='.png', dir=video_dir)
        os.close(fd)
        brand.save(temp_wm, 'PNG')

        ext = os.path.splitext(video_path)[1] or '.mp4'
        fd, temp_out = tempfile.mkstemp(suffix=ext, prefix='.bm_tmp_', dir=video_dir)
        os.close(fd)
        os.unlink(temp_out)

        overlay = ffmpeg_overlay_expr(position, margin)
        cmd = [
            ffmpeg_exe,
            '-y',
            '-i', video_path,
            '-i', temp_wm,
            '-filter_complex',
            f'[1:v]format=rgba[wm];[0:v][wm]overlay={overlay}',
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
            f"Brand-marked video (muted): {video_path}\n"
            f"  size={info['width']}x{info['height']}  "
            f"ratio={info['ratio']:.2f}  "
            f"orientation={info['orientation']}\n"
            f"  brand={target_w}x{target_h}  "
            f"position={POSITION_LABELS[position]}  "
            f"margin={margin}px"
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
        description='Add a corner brand mark to images/videos.'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Only process images in the local ./test folder (skips videos and folder prompt).',
    )
    return parser.parse_args()


def main():
    args = parse_args()

    brand_path = ask_brandmark_path()
    print(f"Using brand mark from: {brand_path}")
    brand_src = prepare_brandmark(brand_path)

    position = ask_position()
    print(f"Placement: {POSITION_LABELS[position]}")

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
        video_paths = []

    analysis_by_path = print_folder_analysis(
        image_paths,
        video_paths=video_paths,
        target_dirs=[os.path.basename(target_folder) or target_folder],
        target_root=target_folder,
        ffmpeg_exe=ffmpeg_exe,
    )

    if not image_paths and not video_paths:
        print("Nothing to brand-mark.")
        return

    image_count = 0
    for path in image_paths:
        add_brandmark_to_image(path, brand_src, position, analysis_by_path.get(path))
        image_count += 1

    video_count = 0
    video_failed = 0
    if video_paths:
        if not ffmpeg_exe:
            print(f"Skipping {len(video_paths)} video(s) — FFmpeg not available.")
            video_failed = len(video_paths)
        else:
            for path in video_paths:
                ok = add_brandmark_to_video(
                    path, brand_src, position, ffmpeg_exe, analysis_by_path.get(path)
                )
                if ok:
                    video_count += 1
                else:
                    video_failed += 1

    print(
        f"\nDone in {target_folder}: "
        f"{image_count} image(s), {video_count} video(s) brand-marked "
        f"at {POSITION_LABELS[position]} (audio removed from videos)"
        + (f", {video_failed} video(s) failed/skipped" if video_failed else "")
        + "."
    )


if __name__ == '__main__':
    main()
