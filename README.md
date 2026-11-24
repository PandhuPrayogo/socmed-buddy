# Socmed-Buddy 🤖✨

**Autonomous Social Media Intelligence & Trend Extraction Agent**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![AI Engine: Google Gemini](https://img.shields.io/badge/AI%20Engine-Google%20Gemini-orange.svg)](https://aistudio.google.com/)
[![Framework: CrewAI](https://img.shields.io/badge/Agent%20Framework-CrewAI-red.svg)](https://crewai.com/)
[![Database: SQLite WAL](https://img.shields.io/badge/Database-SQLite%20WAL-green.svg)](https://sqlite.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Socmed-Buddy is an open-source autonomous agent designed to continuously monitor, curate, and extract actionable insights from social media platforms (**YouTube**, **TikTok**, and **Instagram**) via a modern Telegram interface.

Equipped with a dual-tier Google Gemini architecture and a rule-based citation gate, Socmed-Buddy guarantees that every strategic claim and trend summary is strictly grounded in verifiable source material.

---

## 🌟 Key Highlights

- **Multi-Platform Data Harvesting**:
  - **YouTube**: Direct integration with YouTube Data API v3 using playlist resolution (minimizing API quota usage).
  - **TikTok**: Automated video and transcript scraping via Apify actor integration.
  - **Instagram**: Official Meta Graph API (v21.0) Business Discovery connector for creator metrics and post captions.
- **Dual-Model Agentic Pipeline**:
  - **Extraction Agent (Gemini Flash-Lite)**: High-throughput, sub-second extraction of topics, hooks, and signals per individual post.
  - **Synthesis Agent (Gemini Flash / Pro)**: Deep cross-platform thematic clustering and executive digest generation.
- **Adaptive Circuit Breaker**:
  - Built-in health tracking automatically skips rate-limited models (`429 RESOURCE_EXHAUSTED` or `503 UNAVAILABLE`) and fails over seamlessly to healthy models in milliseconds.
- **Rule-Based Citation Gate**:
  - Deterministic provenance verification. Unverified claims or hallucinated citations are automatically rejected before delivery.
- **Zero-Duplicate Idempotency**:
  - SHA-256 content hashing and atomic SQLite transactions ensure identical posts are never analyzed twice.
- **24/7 Automated Scheduler**:
  - Background JobQueue periodically collects fresh content every 30 minutes, with automatic 30-day raw data retention purges for platform API compliance.

---

## 🏗️ Architecture

```
                 [ Social Media Sources ]
            YouTube  •  TikTok  •  Instagram
                       │
                       ▼
             [ Collector Connectors ]
            (Resolvers, Metrics & Hashes)
                       │
                       ▼
         [ SQLite Database (WAL Mode) ]
     (Targets, Items, Snapshots, Dead Letter)
                       │
                       ▼
              [ CrewAI Agent Flow ]
 ┌──────────────────────────────────────────────┐
 │  1. Content Analyst (Gemini Flash-Lite)      │
 │     • Topic, Format, Hook, Signals           │
 │                                              │
 │  2. Strategic Synthesizer (Gemini Flash/Pro) │
 │     • Thematic Clustering & Synthesis        │
 └──────────────────────┬───────────────────────┘
                        │
                        ▼
           [ Citation Integrity Gate ]
        (Deterministic Provenance Check)
                        │
                        ▼
         [ Telegram Bot User Interface ]
     (Direct Insights, Commands & Visual Reports)
```

---

## 🚀 Quick Start

### 1. Prerequisites
- Python **3.11+**
- Package manager: [`uv`](https://github.com/astral-sh/uv) (recommended) or standard `pip`

### 2. Clone and Install

```bash
# Clone the repository
git clone https://github.com/PandhuPrayogo/socmed-buddy.git
cd socmed-buddy

# Install dependencies using uv
uv sync
```

### 3. Configure Environment

Copy the example configuration file:

```bash
cp .env.example .env
```

Edit `.env` and provide your credentials:

```env
# Required for Telegram interface
TELEGRAM_BOT_TOKEN=your_telegram_bot_token

# Required for AI Analysis (Google AI Studio)
GEMINI_API_KEY=your_gemini_api_key

# Optional: Allowed Telegram Chat IDs (comma-separated, leave blank for open dev testing)
TELEGRAM_ALLOWED_CHAT_IDS=

# Optional: Data Scraper Keys
YOUTUBE_API_KEY=your_youtube_api_key
APIFY_TOKEN=your_apify_token
META_ACCESS_TOKEN=your_meta_access_token
IG_USER_ID=your_instagram_user_id
```

### 4. Run the Agent

```bash
uv run python src/main.py
```

---

## 📱 Telegram Commands

Interact directly with your agent in Telegram:

| Command | Description |
|---|---|
| `/start` | Display the welcome overview and list of available actions |
| `/add <platform> <handle>` | Register a new target account (`youtube`, `tiktok`, `instagram`) |
| `/list` | List all registered monitoring targets and their metrics |
| `/remove <id>` | Unsubscribe target from automated monitoring |
| `/run [platform]` | Trigger an immediate manual collection run |
| `/digest` | Run dual-model agentic analysis and generate the latest digest |
| `/why <id>` | Inspect provenance, metrics, and source text for a cited claim |
| `/status` | View system health, active LLM models, queue depth, and logs |
| `/budget` | Check estimated monthly API costs and remaining balance |
| `/pause` / `/resume` | Pause or resume the 30-minute background collection scheduler |

*Tip: You can also send raw text directly to the bot for on-the-fly topic and hook extraction.*

---

## 🧪 Testing & Verification

Run the comprehensive end-to-end self-check suite:

```bash
uv run python tests/test_system.py
```

The test validates:
1. Database table initialization & WAL mode setup.
2. Target registration & schema validation.
3. Content deduplication idempotency (zero duplicated items).
4. Text length prefiltering & noise reduction.
5. Citation integrity gate (blocking hallucinated claims).
6. 30-day raw data compliance purging.

---

## 🔒 Security & Privacy

- **No Secret Leakage**: Credentials and database records are isolated locally and excluded by `.gitignore`.
- **Restricted Access**: Protect your bot by specifying authorized chat IDs in `TELEGRAM_ALLOWED_CHAT_IDS`.
- **API Terms Compliance**: Raw API payloads older than 30 days are automatically sanitized to adhere to developer policies.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
