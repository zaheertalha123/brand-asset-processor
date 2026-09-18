# Watermarked Assets

Scripts to add center watermarks and corner brand marks to images and videos.

## Setup

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Dependencies

| Package | Purpose |
|---------|---------|
| `Pillow` | Image watermarking and WebP conversion |
| `piexif` | EXIF ownership metadata for images |
| `imageio-ffmpeg` | Bundled FFmpeg for video watermarking / SEO MP4 conversion |

Optional: install system [FFmpeg](https://ffmpeg.org/download.html) and put it on `PATH`. If both exist, system FFmpeg is preferred.

---

## Scripts overview

| Script | What it does |
|--------|----------------|
| `add_watermark.py` | Legacy: top-right brand on `home`, `products`, `works` (images only) |
| `add_watermark_center.py` | Center logo with opacity; images + videos; interactive |
| `add_brandmark.py` | Corner brand mark (UL/UR/LL/LR); images + videos; interactive |
| `seo_enhancements.py` | Convert images → WebP and videos → MP4 (H.264 + AAC) for web/SEO |
| `add_company_metadata.py` | Embed company ownership metadata into images + videos |

Shared logo files:

- `watermark_center.png` — center watermark (figure icon)
- `watermark.png` — corner brand mark (CS logo)

---

## `add_watermark_center.py`

Places a semi-transparent logo in the **center**. Sizes it from each frame’s shorter side so landscape, portrait, and ultrawide stay consistent. Videos are re-encoded and **audio is removed**.

### Run

```bash
python add_watermark_center.py
```

**Prompts**

1. **Watermark image** — full path or filename (Enter = `watermark_center.png` if present)
2. **Target folder** — full path or folder name relative to the current directory

### Test mode (images only)

```bash
python add_watermark_center.py --test
```

Uses `./test` under the current working directory. Skips the folder prompt and skips videos.

### Useful settings (edit at top of file)

```python
OPACITY = 0.35
BASE_SCALE = 0.42
```

---

## `add_brandmark.py`

Places a brand mark in a **corner**, similar to the original top-right style in `add_watermark.py`. Supports all four corners. Videos are re-encoded and **audio is removed**.

### Run

```bash
python add_brandmark.py
```

**Prompts**

1. **Brand-mark image** — full path or filename (Enter = `watermark.png` if present)
2. **Position**
   - `1` Upper Left
   - `2` Upper Right (default if you press Enter)
   - `3` Lower Left
   - `4` Lower Right
3. **Target folder** — full path or folder name relative to the current directory

### Test mode (images only)

```bash
python add_brandmark.py --test
```

### Useful settings (edit at top of file)

```python
BRAND_WIDTH_SCALE = 0.16  # ~16% of frame width
MARGIN = 35
```

---

## `add_watermark.py` (legacy)

Simple batch script: finds `watermark.png` / `logo.png`, walks `home`, `products`, and `works`, and stamps the top-right corner on images only (no prompts, no videos).

```bash
python add_watermark.py
```

Prefer `add_brandmark.py` for new work.

---

## `seo_enhancements.py`

Converts media for web/SEO delivery:

- **Images** (`.jpg`, `.jpeg`, `.png`, `.bmp`, `.tiff`, `.gif`, …) → **`.webp`**
- **Videos** (`.mp4`, `.mov`, `.avi`, `.webm`, `.mkv`, …) → **`.mp4`** with **H.264 + AAC** and `+faststart` for streaming
- Already-`.webp` files are skipped
- Existing `.mp4` files are re-encoded to H.264 + AAC
- Originals are **removed** after a successful conversion (backup first)

### Run

```bash
python seo_enhancements.py
python seo_enhancements.py --folder works
python seo_enhancements.py --test
```

**Prompts / args**

1. **Folder** — prompted unless you pass `--folder PATH` or `--test`
2. **`--test`** — process only `./test` (no prompt)
3. **`--folder` / `-f`** — full path or name relative to the current directory

### Useful settings (edit at top of file)

```python
WEBP_QUALITY = 82
WEBP_METHOD = 6
```

---

## `add_company_metadata.py`

Embeds **company ownership** into media metadata (not a visible watermark):

| Format | What is written |
|--------|------------------|
| JPEG | EXIF Copyright, Artist, ImageDescription |
| WebP | EXIF + XMP rights/creator |
| PNG | PNG text chunks (Copyright, Author, …) + EXIF when supported |
| TIFF | EXIF |
| BMP | Skipped (no useful metadata) |
| MP4 / MOV / other video | Container tags via FFmpeg (`title`, `artist`, `copyright`, `comment`, …) with **stream copy** (no re-encode) |

### Run

```bash
python add_company_metadata.py
python add_company_metadata.py --folder works
python add_company_metadata.py --test
```

**Prompts**

1. **Company name** (required)
2. **Copyright year** (Enter = current year)
3. **Website** (optional)
4. **Folder** — unless `--folder` or `--test`

Example embedded copyright: `© 2026 Your Company. All rights reserved.`

Install dependencies if needed:

```bash
pip install -r requirements.txt
```

---

## Tips

- **Backup first** — all scripts overwrite / replace files in place.
- **`--test`** — copy a few files into `test/`, run with `--test`, check results, then process the real folder.
- **Folder name vs path** — from this project root you can type `works` or `products`; elsewhere use a full path.
- **Videos take longer** — each file is re-encoded with FFmpeg.
- **Subfolders** — scripts walk nested folders and skip non-media files.
- **SEO order tip** — watermark/brand first, then run `seo_enhancements.py` so WebP/MP4 outputs keep your marks.
- **Metadata tip** — run `add_company_metadata.py` after conversions so WebP/JPEG files get the ownership tags; metadata alone is not DRM and can be stripped.
### Example session

```text
Current directory: E:\AstroLinx\watermarked_assets
Enter the watermark image:
  - Press Enter to use default: ...\watermark_center.png
Watermark image: watermark_center.png
Using watermark from: E:\AstroLinx\watermarked_assets\watermark_center.png
Enter the target folder:
Folder: works
```

---

## Requirements file

See `requirements.txt`:

```text
Pillow>=10.0.0
piexif>=1.1.3
imageio-ffmpeg>=0.5.0
```
