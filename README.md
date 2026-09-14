# Clean-Meta 🛡️
> Production-grade AI Metadata Stripper, Lossless Media Cloaker & Optical Camera EXIF Injector.

[![Live Demo](https://img.shields.io/badge/Live%20Demo-clean.primafam.me-0284c7?style=flat&logo=cloudflare)](https://clean.primafam.me)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://python.org)
[![Flask](https://img.shields.io/badge/Framework-Flask-black?logo=flask)](https://flask.palletsprojects.com/)
[![FFmpeg](https://img.shields.io/badge/Video%20Engine-FFmpeg-green?logo=ffmpeg)](https://ffmpeg.org)
[![ExifTool](https://img.shields.io/badge/Forensics-ExifTool-purple)](https://exiftool.org/)

Clean-Meta is a high-performance media forensics utility designed to neutralize AI provenance markers (C2PA Content Credentials, XMP prompts, model identifiers, and generation parameters) from photos and videos while maintaining **100% bit-exact pixel and audio fidelity**.

---

## ⚡ Key Features

- **100% Bit-Exact Image Stripping**: Completely purges C2PA manifests, IPTC tags, and AI prompt chunks without re-encoding or pixel degradation.
- **Authentic Camera EXIF Telemetry**: Synthesizes and injects authentic Apple iPhone 15 Pro Max optical metadata (lens specs, aperture, ISO, shutter speed, timestamps).
- **Lossless Video Stream Copy**: Executes FFmpeg stream copy (-c copy, -map_metadata -1) to sanitize video containers in sub-seconds with zero quality loss.
- **Integrated YouTube & MP3 Pipeline**: Fast media downloader and audio extraction toolchain.
- **PIN Gate Security**: Hardened session authentication using HMAC-SHA256 tokens.
- **Real-Time Job Telemetry**: Asynchronous processing queue with interactive progress streaming and before/after visual inspection.

---

## 🛠️ Tech Stack

- **Backend**: Python 3, Flask, ExifTool, FFmpeg / FFprobe
- **Frontend**: Modern HTML5, Responsive Dark UI, Web Audio API, Vanilla JavaScript
- **Infrastructure**: Linux VPS, Systemd Daemon, Cloudflare Tunnel
- **Live Deployment**: [https://clean.primafam.me](https://clean.primafam.me)

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