"""
SEO enhancements: convert images to WebP and videos to MP4 (H.264 + AAC).
Walks the target folder and all subfolders. Overwrites by replacing originals
with the converted files (originals are removed after a successful conversion).
"""

import argparse
import os
import shutil
import subprocess
import tempfile
from PIL import Image

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.gif')
VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.webm', '.mkv', '.m4v', '.wmv', '.mpeg', '.mpg', '.flv', '.3gp')

# WebP encode quality (0–100). Higher = larger files, better quality.
WEBP_QUALITY = 82
WEBP_METHOD = 6  # 0–6; higher = slower encode, better compression


def resolve_path_input(raw, cwd=None):
    cwd = cwd or os.getcwd()
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return None
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(cwd, raw))


def resolve_target_folder(test_mode=False, folder_arg=None):
    cwd = os.getcwd()

    if test_mode:
        folder = os.path.normpath(os.path.join(cwd, 'test'))
        if not os.path.isdir(folder):
            raise SystemExit(f"Test folder not found: {folder}")
        return folder

    if folder_arg:
        folder = resolve_path_input(folder_arg, cwd)
        if not os.path.isdir(folder):
            raise SystemExit(f"Folder not found: {folder}")
        return folder

    print(f"Current directory: {cwd}")
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


def collect_media(target_dir):
    images = []
    videos = []
    already_webp = []
    other = []

    for root, _, files in os.walk(target_dir):
        for file in files:
            full_path = os.path.join(root, file)
            lower = file.lower()
            if lower.endswith('.webp'):
                already_webp.append(full_path)
            elif lower.endswith(IMAGE_EXTENSIONS):
                images.append(full_path)
            elif lower.endswith(VIDEO_EXTENSIONS):
                videos.append(full_path)
            else:
                other.append(full_path)

    return images, videos, already_webp, other


def convert_image_to_webp(image_path):
    """
    Convert an image to .webp in the same folder (same base name),
    then delete the original file so only the WebP remains.
    Example: works/site/photo.jpg  →  works/site/photo.webp  (jpg removed)
    """
    folder = os.path.dirname(os.path.abspath(image_path)) or '.'
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    out_path = os.path.join(folder, base_name + '.webp')

    if os.path.abspath(image_path) == os.path.abspath(out_path):
        return False, 'already webp'

    try:
        with Image.open(image_path) as im:
            if getattr(im, 'is_animated', False) and getattr(im, 'n_frames', 1) > 1:
                frames = []
                durations = []
                for i in range(im.n_frames):
                    im.seek(i)
                    frames.append(im.convert('RGBA'))
                    durations.append(im.info.get('duration', 100))
                frames[0].save(
                    out_path,
                    format='WEBP',
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=im.info.get('loop', 0),
                    quality=WEBP_QUALITY,
                    method=WEBP_METHOD,
                )
            else:
                if im.mode not in ('RGB', 'RGBA'):
                    im = im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
                im.save(
                    out_path,
                    format='WEBP',
                    quality=WEBP_QUALITY,
                    method=WEBP_METHOD,
                )

        # Original is always removed after a successful write in the same folder
        if os.path.abspath(image_path) != os.path.abspath(out_path):
            os.remove(image_path)

        print(f"Replaced with WebP (same folder): {image_path}  →  {out_path}")
        return True, out_path
    except Exception as e:
        print(f"Error converting image {image_path}: {e}")
        return False, str(e)


def convert_video_to_mp4(video_path, ffmpeg_exe):
    """
    Convert/re-encode video to MP4 with H.264 + AAC, then replace original.
    Temp file is written beside the source (same drive) for Windows safety.
    """
    video_dir = os.path.dirname(os.path.abspath(video_path)) or '.'
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    final_path = os.path.join(video_dir, base_name + '.mp4')

    temp_out = None
    try:
        fd, temp_out = tempfile.mkstemp(suffix='.mp4', prefix='.seo_tmp_', dir=video_dir)
        os.close(fd)
        os.unlink(temp_out)

        # Prefer: encode video; include audio only if present (0:a:0?)
        cmd_safe = [
            ffmpeg_exe,
            '-y',
            '-i', video_path,
            '-map', '0:v:0',
            '-map', '0:a:0?',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ac', '2',
            '-movflags', '+faststart',
            temp_out,
        ]
        result = subprocess.run(cmd_safe, capture_output=True, text=True)

        if result.returncode != 0 or not os.path.exists(temp_out):
            err = (result.stderr or result.stdout or '').strip().splitlines()
            tail = '\n'.join(err[-10:]) if err else 'unknown ffmpeg error'
            raise RuntimeError(tail)

        # If original was already .mp4, replace it; else write .mp4 and remove original
        if os.path.abspath(video_path) == os.path.abspath(final_path):
            os.replace(temp_out, video_path)
            temp_out = None
            print(f"Video → MP4 (H.264+AAC): {video_path}")
            return True, video_path

        if os.path.exists(final_path):
            n = 1
            while os.path.exists(os.path.join(video_dir, f'{base_name}_{n}.mp4')):
                n += 1
            final_path = os.path.join(video_dir, f'{base_name}_{n}.mp4')

        os.replace(temp_out, final_path)
        temp_out = None
        os.remove(video_path)
        print(f"Video → MP4 (H.264+AAC): {video_path}  →  {final_path}")
        return True, final_path
    except Exception as e:
        print(f"Error converting video {video_path}: {e}")
        return False, str(e)
    finally:
        if temp_out and os.path.exists(temp_out):
            try:
                os.unlink(temp_out)
            except OSError:
                pass


def print_plan(target_folder, images, videos, already_webp, other):
    print(f"\n=== SEO enhancements plan ===")
    print(f"Folder: {target_folder}")
    print(f"Images to convert → WebP: {len(images)}")
    print(f"Already WebP (skip):      {len(already_webp)}")
    print(f"Videos to convert → MP4:  {len(videos)} (H.264 + AAC)")
    print(f"Other files skipped:      {len(other)}")
    print("================================\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description='SEO enhancements: convert images to WebP and videos to MP4 (H.264 + AAC).'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Only process the local ./test folder (skips folder prompt).',
    )
    parser.add_argument(
        '--folder',
        '-f',
        metavar='PATH',
        help='Target folder (full path or name relative to current directory).',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    target_folder = resolve_target_folder(test_mode=args.test, folder_arg=args.folder)

    if args.test:
        print(f"Test mode: processing {target_folder}")
    else:
        print(f"Processing folder: {target_folder}")

    images, videos, already_webp, other = collect_media(target_folder)
    print_plan(target_folder, images, videos, already_webp, other)

    if not images and not videos:
        print("Nothing to convert.")
        return

    ffmpeg_exe = None
    if videos:
        try:
            ffmpeg_exe = get_ffmpeg_exe()
            print(f"Using FFmpeg: {ffmpeg_exe}")
        except RuntimeError as e:
            print(f"Warning: {e}")
            print(f"Skipping {len(videos)} video(s).")
            videos = []

    img_ok = img_fail = 0
    for path in images:
        ok, _ = convert_image_to_webp(path)
        if ok:
            img_ok += 1
        else:
            img_fail += 1

    vid_ok = vid_fail = 0
    for path in videos:
        ok, _ = convert_video_to_mp4(path, ffmpeg_exe)
        if ok:
            vid_ok += 1
        else:
            vid_fail += 1

    print(
        f"\nDone in {target_folder}: "
        f"{img_ok} image(s) → WebP"
        + (f", {img_fail} image(s) failed" if img_fail else "")
        + f", {vid_ok} video(s) → MP4 (H.264+AAC)"
        + (f", {vid_fail} video(s) failed" if vid_fail else "")
        + f", {len(already_webp)} WebP skipped."
    )


if __name__ == '__main__':
    main()
