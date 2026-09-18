"""
Add company ownership metadata to images (EXIF / XMP / PNG text)
and videos (MP4/MOV container tags via FFmpeg, stream copy — no re-encode).
Walks a folder and all subfolders.
"""

import argparse
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from xml.sax.saxutils import escape

from PIL import Image
from PIL.PngImagePlugin import PngInfo

try:
    import piexif
except ImportError as e:
    raise SystemExit(
        "Missing dependency: piexif\nInstall with: pip install -r requirements.txt"
    ) from e

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.tiff', '.tif', '.bmp')
VIDEO_EXTENSIONS = ('.mp4', '.mov', '.m4v', '.mkv', '.webm', '.avi', '.wmv', '.mpeg', '.mpg')


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


def ask_company_info():
    """Prompt for company name and copyright year."""
    print("Enter company details for media metadata:")
    company = input("Company name: ").strip()
    if not company:
        raise SystemExit("Company name is required. Exiting.")

    year_default = str(datetime.now().year)
    year_raw = input(f"Copyright year [{year_default}]: ").strip()
    year = year_raw if year_raw.isdigit() else year_default

    website = input("Company website (optional): ").strip()
    copyright_text = f"© {year} {company}. All rights reserved."
    description = f"Owned by {company}."
    if website:
        description = f"{description} {website}"

    return {
        'company': company,
        'year': year,
        'website': website,
        'copyright': copyright_text,
        'description': description,
        'artist': company,
        'title': f"{company} media asset",
    }


def _enc(value):
    """Encode a string for piexif (UTF-8 bytes)."""
    if isinstance(value, bytes):
        return value
    return str(value).encode('utf-8')


def build_exif_bytes(meta, existing_exif=None):
    """Build EXIF blob with Copyright, Artist, and ImageDescription."""
    if existing_exif:
        try:
            exif_dict = piexif.load(existing_exif)
        except Exception:
            exif_dict = {'0th': {}, 'Exif': {}, 'GPS': {}, '1st': {}}
    else:
        exif_dict = {'0th': {}, 'Exif': {}, 'GPS': {}, '1st': {}}

    for key in ('0th', 'Exif', 'GPS', '1st'):
        if key not in exif_dict or exif_dict[key] is None:
            exif_dict[key] = {}

    exif_dict['0th'][piexif.ImageIFD.Copyright] = _enc(meta['copyright'])
    exif_dict['0th'][piexif.ImageIFD.Artist] = _enc(meta['artist'])
    exif_dict['0th'][piexif.ImageIFD.ImageDescription] = _enc(meta['description'])
    exif_dict['0th'][piexif.ImageIFD.Software] = _enc('AstroLinx metadata tool')

    if 'thumbnail' in exif_dict:
        exif_dict['thumbnail'] = None

    try:
        return piexif.dump(exif_dict)
    except Exception:
        clean = {
            '0th': {
                piexif.ImageIFD.Copyright: _enc(meta['copyright']),
                piexif.ImageIFD.Artist: _enc(meta['artist']),
                piexif.ImageIFD.ImageDescription: _enc(meta['description']),
                piexif.ImageIFD.Software: _enc('AstroLinx metadata tool'),
            },
            'Exif': {},
            'GPS': {},
            '1st': {},
            'thumbnail': None,
        }
        return piexif.dump(clean)


def build_xmp_packet(meta):
    """Minimal XMP rights packet (useful for WebP / broad tooling)."""
    company = escape(meta['company'])
    copyright_text = escape(meta['copyright'])
    description = escape(meta['description'])
    website = escape(meta['website']) if meta.get('website') else ''

    web_line = f'\n   <xmpRights:WebStatement>{website}</xmpRights:WebStatement>' if website else ''

    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '   xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
        '   xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/"\n'
        '   xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">\n'
        f'   <dc:rights>\n'
        f'    <rdf:Alt>\n'
        f'     <rdf:li xml:lang="x-default">{copyright_text}</rdf:li>\n'
        f'    </rdf:Alt>\n'
        f'   </dc:rights>\n'
        f'   <dc:creator>\n'
        f'    <rdf:Seq>\n'
        f'     <rdf:li>{company}</rdf:li>\n'
        f'    </rdf:Seq>\n'
        f'   </dc:creator>\n'
        f'   <dc:description>\n'
        f'    <rdf:Alt>\n'
        f'     <rdf:li xml:lang="x-default">{description}</rdf:li>\n'
        f'    </rdf:Alt>\n'
        f'   </dc:description>\n'
        f'   <photoshop:Credit>{company}</photoshop:Credit>\n'
        f'   <photoshop:Source>{company}</photoshop:Source>\n'
        f'   <xmpRights:Marked>True</xmpRights:Marked>{web_line}\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>'
    ).encode('utf-8')


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
    for root, _, files in os.walk(target_dir):
        for file in files:
            full_path = os.path.join(root, file)
            lower = file.lower()
            if lower.endswith(IMAGE_EXTENSIONS):
                images.append(full_path)
            elif lower.endswith(VIDEO_EXTENSIONS):
                videos.append(full_path)
    return images, videos


def apply_metadata_to_image(image_path, meta):
    """Write company ownership metadata into the image file in place."""
    ext = os.path.splitext(image_path)[1].lower()
    try:
        with Image.open(image_path) as im:
            existing = im.info.get('exif')
            exif_bytes = build_exif_bytes(meta, existing)
            xmp_bytes = build_xmp_packet(meta)

            if ext in ('.jpg', '.jpeg'):
                im.save(image_path, format='JPEG', quality=95, exif=exif_bytes)

            elif ext == '.webp':
                save_kwargs = {
                    'format': 'WEBP',
                    'quality': 90,
                    'method': 6,
                    'exif': exif_bytes,
                }
                try:
                    im.save(image_path, xmp=xmp_bytes, **save_kwargs)
                except TypeError:
                    im.save(image_path, **save_kwargs)

            elif ext == '.png':
                pnginfo = PngInfo()
                pnginfo.add_text('Copyright', meta['copyright'])
                pnginfo.add_text('Author', meta['company'])
                pnginfo.add_text('Description', meta['description'])
                pnginfo.add_text('Source', meta['company'])
                if meta.get('website'):
                    pnginfo.add_text('Website', meta['website'])
                try:
                    im.save(image_path, format='PNG', pnginfo=pnginfo, exif=exif_bytes)
                except Exception:
                    im.save(image_path, format='PNG', pnginfo=pnginfo)

            elif ext in ('.tif', '.tiff'):
                im.save(image_path, format='TIFF', exif=exif_bytes)

            elif ext == '.bmp':
                print(f"Skipped (BMP has no metadata support): {image_path}")
                return False

            else:
                print(f"Skipped (unsupported for metadata): {image_path}")
                return False

        print(
            f"Image metadata updated: {image_path}\n"
            f"  Copyright: {meta['copyright']}\n"
            f"  Artist/Owner: {meta['company']}"
        )
        return True
    except Exception as e:
        print(f"Error updating image {image_path}: {e}")
        return False


def apply_metadata_to_video(video_path, meta, ffmpeg_exe):
    """
    Write company tags into the video container with stream copy (no re-encode).
    Temp file stays on the same drive as the source (Windows-safe replace).
    """
    video_dir = os.path.dirname(os.path.abspath(video_path)) or '.'
    ext = os.path.splitext(video_path)[1] or '.mp4'
    temp_out = None

    try:
        fd, temp_out = tempfile.mkstemp(suffix=ext, prefix='.meta_tmp_', dir=video_dir)
        os.close(fd)
        os.unlink(temp_out)

        cmd = [
            ffmpeg_exe,
            '-y',
            '-i', video_path,
            '-c', 'copy',
            '-map_metadata', '0',
            '-metadata', f"title={meta['title']}",
            '-metadata', f"artist={meta['artist']}",
            '-metadata', f"author={meta['company']}",
            '-metadata', f"copyright={meta['copyright']}",
            '-metadata', f"comment={meta['description']}",
            '-metadata', f"description={meta['description']}",
            '-metadata', f"publisher={meta['company']}",
        ]
        if meta.get('website'):
            cmd.extend(['-metadata', f"purl={meta['website']}"])

        # MP4/MOV often need explicit movflags for metadata write
        if ext.lower() in ('.mp4', '.m4v', '.mov'):
            cmd.extend(['-movflags', 'use_metadata_tags'])

        cmd.append(temp_out)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(temp_out):
            # Retry without movflags (some containers dislike it)
            cmd_retry = [
                ffmpeg_exe,
                '-y',
                '-i', video_path,
                '-c', 'copy',
                '-map_metadata', '0',
                '-metadata', f"title={meta['title']}",
                '-metadata', f"artist={meta['artist']}",
                '-metadata', f"copyright={meta['copyright']}",
                '-metadata', f"comment={meta['description']}",
                '-metadata', f"description={meta['description']}",
                temp_out,
            ]
            result = subprocess.run(cmd_retry, capture_output=True, text=True)

        if result.returncode != 0 or not os.path.exists(temp_out):
            err = (result.stderr or result.stdout or '').strip().splitlines()
            tail = '\n'.join(err[-10:]) if err else 'unknown ffmpeg error'
            raise RuntimeError(tail)

        os.replace(temp_out, video_path)
        temp_out = None

        print(
            f"Video metadata updated: {video_path}\n"
            f"  Copyright: {meta['copyright']}\n"
            f"  Artist/Owner: {meta['company']}"
        )
        return True
    except Exception as e:
        print(f"Error updating video {video_path}: {e}")
        return False
    finally:
        if temp_out and os.path.exists(temp_out):
            try:
                os.unlink(temp_out)
            except OSError:
                pass


def parse_args():
    parser = argparse.ArgumentParser(
        description='Add company ownership metadata to images and videos in a folder.'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Only process media in the local ./test folder.',
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
    meta = ask_company_info()
    print(f"\nWill embed:\n  {meta['copyright']}\n  {meta['description']}\n")

    target_folder = resolve_target_folder(test_mode=args.test, folder_arg=args.folder)
    if args.test:
        print(f"Test mode: processing {target_folder}")
    else:
        print(f"Processing folder: {target_folder}")

    images, videos = collect_media(target_folder)
    print(f"\nImages found: {len(images)}")
    print(f"Videos found: {len(videos)}")

    if not images and not videos:
        print("Nothing to update.")
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
        if apply_metadata_to_image(path, meta):
            img_ok += 1
        else:
            img_fail += 1

    vid_ok = vid_fail = 0
    for path in videos:
        if apply_metadata_to_video(path, meta, ffmpeg_exe):
            vid_ok += 1
        else:
            vid_fail += 1

    print(
        f"\nDone in {target_folder}: "
        f"{img_ok} image(s)"
        + (f" ({img_fail} failed/skipped)" if img_fail else "")
        + f", {vid_ok} video(s)"
        + (f" ({vid_fail} failed/skipped)" if vid_fail else "")
        + " updated."
    )
    print(
        "Note: metadata can be stripped by some apps/exports; "
        "keep visible watermarks for stronger ownership marking."
    )


if __name__ == '__main__':
    main()
