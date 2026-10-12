# Brick Tidy – LEGO Cleanup Assistant

A self-hosted web app that helps restore partially assembled LEGO sets from large mixed boxes.
Open it on your phone, take photos, and get a list of what is missing and which bricks in a pile match.

## Getting started

```bash
# Docker
docker compose run --rm brick-tidy python /app/scripts/import_rebrickable.py --download
docker compose up -d          # then open http://<host-ip>:8080 on the phone

# Local development
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements-dev.txt
backend/.venv/bin/python scripts/import_rebrickable.py --download   # ~20 s, into ./data
cd backend && .venv/bin/uvicorn app.main:create_app --factory --reload --host 0.0.0.0 --port 8080
.venv/bin/python -m pytest    # tests (run from backend/)
```

Copy `.env.example` to `.env` to change settings. The catalog import can be re-run at any time to
refresh the Rebrickable data; projects are kept.

**Status:** roadmap steps 1–4 are implemented:

- **Set identification:** start a project by typing the set number, or by photographing the box,
  instructions or partial build. Brickognize suggests sets; when it is unsure, fails or finds nothing,
  Claude is asked too (only if `ANTHROPIC_API_KEY` is set). You pick the set from the suggestions.
- **Detection and recognition:** photograph a small batch of bricks spread out on a plain surface;
  each brick is cropped (OpenCV), recognized by Brickognize, and offered for review.
- **Find bricks:** photos of loose bricks are shown with a box around each brick; parts the set still
  needs are highlighted. Tap a box and pick the brick's color to mark it as found. Colors are not
  recognized yet, so a highlighted part may be needed only in another color.
- **Review and missing list:** confirm or correct each brick, list parts by hand, view the missing
  list and export it as CSV. Brickognize returns BrickLink part IDs. These are mapped to the set's
  Rebrickable part numbers via Rebrickable's mold, alternate and print relationships
  (e.g. 61678 → 11153).

Brickognize's keyless endpoints are marked "legacy" in its API; it also offers a paid API with
credits. If the free endpoints become unavailable, brick recognition falls back to manual review.

## Goals

Minimal required features:

1. **Identify the set** that a partially assembled model belongs to (from a phone photo).
2. **Find the missing bricks** by comparing what is present against the set's official parts list.
3. **Detect the missing bricks** in new photos (or a series of photos) of bricks on the floor or in a box.

Non-goals (for now): native Android/iOS apps, fully automatic recognition without human confirmation,
sorting hardware, multi-user accounts.

## Key design decisions

| Decision | Rationale |
|---|---|
| Web app instead of native app | No app store, one codebase, works on any phone browser |
| Phone camera via `<input type="file" capture="environment">` | Works over plain HTTP, uses the phone's native camera (autofocus, HDR, full resolution) |
| Hybrid pipeline (LLM + classic CV + recognition API) | Each component is used where it is strongest |
| Diff against the official parts inventory, not against a "before" photo | Far more reliable than image-to-image comparison |
| Human-in-the-loop confirmation | Recognition is never perfect on messy piles; review is cheap |

## High-level architecture

```
 ┌──────────────┐        HTTP(S)        ┌───────────────────────────────────────┐
 │  Phone       │ ────────────────────► │  Backend (FastAPI, Docker)            │
 │  Browser/PWA │ ◄──────────────────── │                                       │
 │  (camera UI) │     JSON + images     │  ┌───────────┐  ┌──────────────────┐  │
 └──────────────┘                       │  │ Set       │  │ Detection &      │  │
                                        │  │ Identifier│  │ Cropping         │  │
                                        │  │ (LLM)     │  │ (OpenCV / YOLO)  │  │
                                        │  └─────┬─────┘  └────────┬─────────┘  │
                                        │        │                 │            │
                                        │  ┌─────▼─────┐  ┌────────▼─────────┐  │
                                        │  │ Inventory │  │ Part Recognizer  │  │
                                        │  │ Service   │  │ (Brickognize API)│  │
                                        │  │(Rebrickable)│ │ + LLM fallback   │  │
                                        │  └─────┬─────┘  └────────┬─────────┘  │
                                        │        └────────┬────────┘            │
                                        │           ┌─────▼─────┐               │
                                        │           │ Diff /    │               │
                                        │           │ Matching  │               │
                                        │           └─────┬─────┘               │
                                        │           ┌─────▼─────┐               │
                                        │           │ SQLite +  │               │
                                        │           │ image dir │               │
                                        │           └───────────┘               │
                                        └───────────────────────────────────────┘
                                                │                │
                                    Rebrickable CSV / API   Brickognize API, LLM API
                                       (internet)              (internet)
```

## Components

### 1. Frontend (phone web UI)
- Plain HTML + small JS (or a light framework); installable as a PWA.
- Pages: *Projects* (one per set), *Capture* (take/upload photos), *Review* (confirm or correct
  detected parts), *Missing list*, *Find bricks* (highlight matches in a pile photo).
- Uses the native camera through the file input; no HTTPS required for this.

### 2. Set Identifier (Brickognize, LLM fallback)
- Input: photo(s) of the partial build, the box, stickers, or loose pieces.
- Primary: Brickognize set recognition (free, no key). Fallback: a vision LLM (Claude) when
  Brickognize is unsure or unavailable.
- Output: up to 3–5 candidate sets with confidence and any set number read from the image.
- The user confirms the set; this keeps the weakest step cheap and safe.
- Alternative path: user types the set number directly.

### 3. Inventory Service (Rebrickable)
- Imports Rebrickable CSV downloads (sets, inventories, inventory_parts, parts, colors) into SQLite.
- Optionally queries the Rebrickable API for updates.
- Provides: `get_inventory(set_num) -> [(part_id, color_id, quantity)]`.

### 4. Detection & Cropping (OpenCV / YOLO)
- Finds individual bricks in a photo and produces one crop per brick.
- Start with classic OpenCV segmentation on a plain background; upgrade to a small YOLO model
  (or reuse ideas from `thomas-rooty/lego-detector`) for cluttered scenes.
- Photograph small batches on a neutral surface for best results.

### 5. Part Recognizer
- Primary: **Brickognize API** (part ID + color + confidence per crop).
- Fallback: for low-confidence crops, send only that crop to a vision LLM or flag for manual review.
- Color normalisation step maps results to Rebrickable color IDs.

### 6. Diff / Matching
- **Missing list** = set inventory − (parts confirmed present in the partial build + parts already found).
- **Find bricks** = recognized parts in a pile photo ∩ missing list, shown as highlighted crops.
- State is kept per project, so found parts reduce the missing list over time.

### 7. Storage
- SQLite for projects, inventories, recognized parts, and confirmations.
- A simple directory for original photos and crops.

## Main workflows

**A. Start a project**
1. Photograph the partial build / box → LLM proposes sets → user confirms (or types set number).
2. Backend loads the set inventory.

**B. Determine what is missing**
1. Photograph the build in several parts/angles (or list parts by hand).
2. Detect → crop → recognize → user reviews and confirms.
3. Diff against inventory → missing parts list.

**C. Find missing bricks in the boxes**
1. Photograph a small batch of loose bricks.
2. Detect → crop → recognize.
3. Matches against the missing list are highlighted; user confirms and marks them as found.

## Tech stack (proposed)

- Python 3.11+, FastAPI, Uvicorn
- OpenCV, optionally Ultralytics YOLO
- SQLite (SQLAlchemy or plain `sqlite3`)
- Docker / Docker Compose (laptop or Synology Container Manager)
- External services: Brickognize API, Rebrickable data, a vision-capable LLM API

## Deployment

- **Laptop:** `docker compose up`, open `http://<laptop-ip>:8080` on the phone (same Wi-Fi).
- **Synology NAS:** same container via Container Manager; run the heavy detection on a stronger machine if the NAS CPU is weak.
- **Camera / HTTPS:** the file-input capture works over plain HTTP. For live camera preview or
  remote access, use HTTPS (Synology reverse proxy + Let's Encrypt, Tailscale, or mkcert).

## Suggested project structure

```
brick-tidy/
├── README.md
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI entry point
│   │   ├── api/               # routes: projects, photos, review, missing
│   │   ├── services/
│   │   │   ├── set_identifier.py
│   │   │   ├── inventory.py
│   │   │   ├── detector.py
│   │   │   ├── recognizer.py
│   │   │   └── matcher.py
│   │   ├── db.py
│   │   └── config.py
│   └── requirements.txt
├── frontend/                  # static pages / PWA manifest
├── data/                      # SQLite DB, Rebrickable CSVs, photos, crops
└── scripts/
    └── import_rebrickable.py
```

## Implementation roadmap

1. **MVP:** project + set number input, Rebrickable import, photo upload, Brickognize call on a
   single-brick photo, manual review, missing list.
2. **Detection:** crop multiple bricks per photo (OpenCV), batch recognition.
3. **Set identification:** LLM candidate suggestions from photos.
4. **Find bricks:** highlight matches from the missing list in pile photos.
5. **Polish:** PWA install, low-confidence fallback, color normalisation, export of the missing list
   (e.g. for BrickLink wanted lists).

## Configuration

Environment variables (example):

```
BRICKOGNIZE_URL=https://api.brickognize.com/predict/
BRICKOGNIZE_SETS_URL=https://api.brickognize.com/predict/sets/
REBRICKABLE_API_KEY=...        # optional, CSV import works without it
ANTHROPIC_API_KEY=...          # optional, Claude as set identification fallback
CLAUDE_MODEL=claude-opus-5-5
CONFIDENCE_THRESHOLD=0.5
DATA_DIR=./data
```

See `.env.example`.

## Known limitations

- Recognizing a *partially assembled* model's parts is hard (occlusion); expect to confirm manually.
- Similar colors (e.g. light gray vs. light bluish gray) and rare part variants are error-prone.
- Piles of many bricks work much worse than small batches on a plain background.
- Brickognize and LLM calls require internet access and are subject to the providers' terms and limits.

## Related open-source projects

- [LegoSorter](https://github.com/BDvok2/LegoSorter) – phone UI + Brickognize + color detection
- [lego-detector](https://github.com/thomas-rooty/lego-detector) – OpenCV detection + TensorFlow classification
- [LegoBrickClassification](https://github.com/jtheiner/LegoBrickClassification) – synthetic data + classifier

LEGO® is a trademark of the LEGO Group, which does not sponsor, authorize or endorse this project.
