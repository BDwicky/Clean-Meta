# Clean-Meta 🛡️
> Production-grade AI Metadata Stripper, Lossless Media Cloaker & Optical Camera EXIF Injector.

[![Live Demo](https://img.shields.io/badge/Live%20Demo-clean.dwicky.dev-0284c7?style=flat&logo=cloudflare)](https://clean.dwicky.dev)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://python.org)
[![Flask](https://img.shields.io/badge/Framework-Flask-black?logo=flask)](https://flask.palletsprojects.com/)
[![FFmpeg](https://img.shields.io/badge/Video%20Engine-FFmpeg-green?logo=ffmpeg)](https://ffmpeg.org)
[![ExifTool](https://img.shields.io/badge/Forensics-ExifTool-purple)](https://exiftool.org/)

Clean-Meta is a high-performance media forensics utility designed to neutralize AI provenance markers (C2PA Content Credentials, XMP prompts, model identifiers, and generation parameters) from photos and videos while maintaining **100% bit-exact pixel and audio fidelity**.

---

## ⚡ Key Features

- **Open Public Access**: Zero friction media sanitization, video cloaking, YouTube harvester, and MP3 extractor.
- **100% Bit-Exact Image Stripping**: Completely purges C2PA manifests, IPTC tags, and AI prompt chunks without re-encoding or pixel degradation.
- **Multi-Camera EXIF Telemetry**: Synthesizes and injects authentic optical metadata from Apple iPhone 15 Pro Max, Samsung Galaxy S24 Ultra, Google Pixel 8 Pro, Sony A7 IV, Canon EOS R5, and Fujifilm X-T5.
- **Lossless Video Stream Copy**: Executes FFmpeg stream copy (-c copy, -map_metadata -1) to sanitize video containers in sub-seconds with zero quality loss.
- **Isolated Client History (12h TTL)**: Per-client session history managed via server SQLite database.
- **Admin Mode & Retention Freeze**: Admin can unlock system-wide client logs and toggle "Tidak Hangus" (permanent preservation) to bypass automatic file cleanup.
- **Real-Time Job Telemetry**: Asynchronous processing queue with interactive progress streaming and before/after visual inspection.

---

## 🛠️ Tech Stack

- **Backend**: Python 3, Flask, SQLite3, ExifTool, FFmpeg / FFprobe, yt-dlp
- **Frontend**: Modern HTML5, Responsive Dark UI, Web Audio API, Vanilla JavaScript
- **Infrastructure**: Linux VPS, Systemd Daemon, Cloudflare Tunnel
- **Live Deployment**: [https://clean.dwicky.dev](https://clean.dwicky.dev)

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- fmpeg & fprobe
- xiftool

### Installation
\\\ash
# Clone the repository
git clone https://github.com/BDwicky/Clean-Meta.git
cd Clean-Meta

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install flask yt-dlp

# Run server
python server.py
\\\

---

## 📜 License
MIT License. Crafted with precision by [Bagus Dwicy Primananda](https://github.com/BDwicky).