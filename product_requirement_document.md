# Product Requirement Document (PRD)
## Open-Source Masjid Digital Display System (*MasjidDisplay*)

---

| Document Detail | Information |
| :--- | :--- |
| **Project Name** | MasjidDisplay (Working Title) |
| **Status** | Proposed |
| **Target Platforms** | Linux (Debian / Raspberry Pi OS), x86/ARM devices |
| **Primary Region Focus**| Malaysia (JAKIM Integration), extensible globally |
| **License** | Open Source (MIT or Apache 2.0) |

---

## 1. Executive Summary & Product Vision

### 1.1 Vision Statement
To create an accessible, lightweight, modern, and open-source digital signage system for mosques (*masjid*) and prayer halls (*surau*). The platform aims to bridge technical barriers for non-technical mosque administrators while providing a low-power, robust display system that functions seamlessly both online and offline.

### 1.2 Core Objectives
* **Zero Friction Management:** Allow mosque admins to configure settings via a simple local web interface without editing config files.
* **Resilient Dual-Mode Operation:** Seamlessly switch between online API sync (e.g., JAKIM E-Solat) and local, pre-loaded yearly offline schedules.
* **Ultra-Low Resource Footprint:** Run efficiently on hardware as minimal as a Raspberry Pi Zero 2 W or an old Debian-based thin client.
* **Distraction-Free Prayer Environment:** Automatically dim/blackout display during active prayer times to maintain solemnity.
* **Modular Multi-Display & Theming:** Support standalone or networked multi-screen setups with customizable and community-contributed visual themes.

---

## 2. User Personas & Key Use Cases

### 2.1 Personas

```
                  +-----------------------------------+
                  |        MOSQUE COMMUNITY           |
                  +-----------------------------------+
                                    |
        +---------------------------+---------------------------+
        |                           |                           |
        v                           v                           v
+---------------+           +---------------+           +---------------+
|  1. Jama'ah   |           | 2. Mosque     |           | 3. System     |
|   (Public)    |           |    Admin      |           |    Integrator |
+---------------+           +---------------+           +---------------+
| Reads times,  |           | Configures    |           | Installs hardware,|
| countdowns,   |           | settings,     |           | creates custom    |
| announcements |           | uploads images|           | community themes  |
+---------------+           +---------------+           +---------------+
```

1. **Jama'ah (Public Visitor / Worshipper):** Needs clear, readable prayer/Iqomah times, count-downs, and announcements without visual clutter or distraction.
2. **Mosque Admin (Non-Technical Staff/Committee):** Needs an intuitive web dashboard accessible via phone or PC to adjust Iqomah buffers, upload posters, or change themes easily.
3. **System Integrator / Community Developer:** Technical volunteers who install the system, contribute new themes, or add integrations for international prayer calculation methods.

---

## 3. Functional Requirements

### 3.1 Prayer Time Engine (Core Service)

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-1.1** | **JAKIM API Sync** | Auto-fetch daily/monthly prayer times from JAKIM E-Solat API based on zone codes (e.g., `SGR01`, `WKP01`). | High |
| **FR-1.2** | **Offline Mode (Local Storage)** | Support pre-loaded SQLite/JSON database containing 365-day schedules for all Malaysian zones + offline fallback logic when internet drops. | High |
| **FR-1.3** | **International Calculation Engine** | Include built-in prayer calculation algorithms (e.g., ISNA, MWL, Egyptian, MABIMS) for international users based on latitude/longitude. | Medium |
| **FR-1.4** | **Iqomah Buffer Management** | Configurable delay timers per prayer (e.g., 10 mins post-Adhan for Dhuhr, 15 mins for Isha) or manual fixed Iqomah times. | High |
| **FR-1.5** | **Hijri Date Auto-Correction** | Calculate Hijri date with a manual offset adjustment tool (-2 to +2 days) to match regional moonsighting declarations. | High |

### 3.2 Display Client Interface (Public View)

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-2.1** | **Main Dashboard View** | Display real-time clock, current date (Gregorian & Hijri), 5 daily prayer times + Sunrise/Syuruq, next prayer indicator, and Iqomah countdown. | High |
| **FR-2.2** | **Adhan Alert Overlay** | Full-screen visual state when Adhan time triggers, accompanied by an optional local audio chime or Adhan notification. | High |
| **FR-2.3** | **Iqomah Countdown Mode** | Prominent live countdown timer counting down to prayer start once Adhan period ends. | High |
| **FR-2.4** | **Prayer Dimming / Blackout** | Automagically dim or blacken screen (showing only minimalist clock or completely blank) starting at Iqomah time for a user-set duration (e.g., 15–30 mins). | High |
| **FR-2.5** | **Responsive / TV Scaling** | Auto-scale cleanly across 720p, 1080p, and 4K displays without layout breaking. | High |

### 3.3 Information Carousel Module

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-3.1** | **Image Slideshow** | Cycle through uploaded banners, Hadith of the Day, event announcements, or donation QR codes. | Medium |
| **FR-3.2** | **Toggleable Module** | Option to completely disable carousel for mosques wanting a pure prayer-time-only aesthetic. | High |
| **FR-3.3** | **Carousel Pause Rule** | Automatically hide carousel 5 minutes before Adhan and remain hidden through prayer time. | High |

### 3.4 Multi-Display & Grouping Management

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-4.1** | **Display Registration** | Detect and list all connected display clients on the local network via unique Display IDs or IP addresses. | Medium |
| **FR-4.2** | **Display Grouping** | Allow admins to group displays (e.g., "Main Hall", "Entrance Lobby", "Women's Section"). | Low |
| **FR-4.3** | **Targeted Configurations** | Option to apply settings globally (all screens) or assign specific themes/carousels to specific display groups. | Medium |

### 3.5 Theme & Customization Engine

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-5.1** | **Pre-installed Themes** | Package app with at least 3 distinct themes (e.g., Classic Modern Green, Minimalist Dark, Informational Board). | High |
| **FR-5.2** | **Community Theme Structure** | Themes structured as plain HTML/CSS/JS template directories that can be uploaded via zip file in the Admin Panel. | High |
| **FR-5.3** | **Live Theme Preview** | Preview theme layouts in the admin dashboard before publishing live to public screens. | Medium |

### 3.6 Admin Control Panel

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-6.1** | **Mobile-Friendly UI** | Responsive web interface designed for easy phone or tablet administration. | High |
| **FR-6.2** | **Setup Wizard** | First-time boot wizard asking for Location/Zone, Mode (Online/Offline), and Basic Masjid Info. | High |
| **FR-6.3** | **QR Code Fast-Connect** | Display an admin access QR code on the TV boot screen for quick phone connection over local Wi-Fi/LAN. | High |

---

## 4. Technical Architecture & Tech Stack Evaluation

### 4.1 Tech Stack Evaluation

To ensure the system remains simple, mature, community-friendly, and lightweight, four primary technology stack combinations were evaluated:

| Criteria | Option A: Node.js + Vue.js | Option B: Python (FastAPI) + HTMX/Alpine | Option C: Go + Embedded Frontend | Option D: PHP (Laravel) + SQLite |
| :--- | :--- | :--- | :--- | :--- |
| **RAM Footprint** | ~120 - 200 MB | **~40 - 70 MB** | **~15 - 30 MB** | ~150 - 250 MB |
| **Build/Dev Complexity** | High (npm, Vite bundling) | **Very Low (No build step needed)**| Medium (Go toolchain required) | Medium (Composer, Nginx setup) |
| **Community Accessibility** | High | **Extremely High** | Moderate | High in regional areas |
| **Single-Binary / Deploy** | Requires Node runtime | Requires Python runtime | **Single compiled binary** | Requires PHP-FPM runtime |
| **Maintainability** | Moderate (dep drift) | **High (Standard Library heavy)** | High | Moderate |

### 4.2 Selected Tech Stack Recommendation

**Selected Stack: Option B — Python 3.11+ (FastAPI) + SQLite + HTMX / Alpine.js + Tailwind CSS**

#### Justification for Selection:
1. **Maturity & Python Ecosystem:** Python is natively installed on Debian/Raspberry Pi OS. It has standard libraries for scheduling (`APScheduler`), SQLite support, and low-level system commands.
2. **FastAPI Efficiency:** High performance, asynchronous handling for web requests and real-time updates (WebSockets/SSE), with built-in automatic API documentation (OpenAPI).
3. **No Frontend Build Step:** By pairing HTMX and Alpine.js with Jinja2 templates, developers do **not** need Node/npm build steps (Webpack/Vite). Themes are plain HTML/CSS/JS templates, making it exceptionally community-friendly for open-source contributors.
4. **SQLite Zero-Maintenance:** Uses a single local file database requiring zero setup, administration, or external database engines.

```
+-----------------------------------------------------------------------+
|                           MASJIDDISPLAY STACK                          |
+-----------------------------------------------------------------------+
|  Frontend Layer   |  HTML5 + Tailwind CSS + Alpine.js + HTMX           |
|  Realtime Sync    |  Server-Sent Events (SSE) / WebSockets             |
|  Application Server|  Python 3.11+ with FastAPI & Uvicorn                |
|  Database Layer   |  SQLite3 (Embedded)                                |
|  OS & Runtime     |  Debian Linux / Raspberry Pi OS (Systemd Service)   |
|  Client Display   |  Chromium Browser (Kiosk Mode)                     |
+-----------------------------------------------------------------------+
```

---

## 5. Non-Functional Requirements (NFR)

### 5.1 Performance & Resource Limits
* **RAM Usage:** Total application background RAM usage must not exceed **80 MB** on idle.
* **CPU Usage:** Idle CPU utilization must remain below **3%** on a Raspberry Pi 3B+.
* **Boot Time:** Application service must start and render prayer times within **15 seconds** of OS network initialization.

### 5.2 Reliability & Fault Tolerance
* **Offline First:** System must operate indefinitely without internet once initialized.
* **Network Recovery:** If network drops during online mode, the system auto-falls back to local offline tables and retries connections silently in the background.
* **Power Cut Safety:** Database writes must use SQLite WAL (Write-Ahead Logging) mode to prevent corruption during sudden power loss.

### 5.3 Security
* **Local Auth:** Password-protected Admin UI using Argon2 or bcrypt hashing.
* **Network Isolation:** Runs completely on local network (LAN); no mandatory cloud dependencies or external inbound open ports required.
* **Input Validation:** Strict sanitization on custom theme uploads and image asset management to prevent arbitrary code execution.

---

## 6. Data Specifications & API Protocols

### 6.1 JAKIM API Request Schema Example
When configured for Malaysian zones, the backend polls:
```http
GET https://www.e-solat.gov.my/index.php?r=esolatApi/takwimsolat&period=today&zone=SGR01
```

### 6.2 Local SQLite Schema Draft

```sql
-- Application Settings
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Offline Prayer Times
CREATE TABLE prayer_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date_gregorian DATE NOT NULL,
    date_hijri TEXT,
    fajr TIME NOT NULL,
    syuruq TIME NOT NULL,
    dhuhr TIME NOT NULL,
    asr TIME NOT NULL,
    maghrib TIME NOT NULL,
    isha TIME NOT NULL,
    zone_code TEXT DEFAULT 'DEFAULT'
);

-- Display Screen Registry
CREATE TABLE displays (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ip_address TEXT,
    group_name TEXT DEFAULT 'Default',
    current_theme TEXT DEFAULT 'classic-green',
    last_seen DATETIME
);
```

---

## 7. Deployment & Operating System Integration

### 7.1 Single-Board Computer / Linux Setup
The deployment strategy relies on standard Linux utilities for zero-cost maintenance:

1. **System Service:** `masjid-display.service` managed by `systemd` to handle auto-restart on crashes or system reboots.
2. **Kiosk Mode Display:** Lightweight X11 window manager (`matchbox-window-manager` or `openbox`) executing Chromium in kiosk mode:
   ```bash
   chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:8000/display
   ```
3. **HDMI CEC Control (Optional):** Integration with `cec-utils` to automatically turn connected TV displays ON before Fajr and OFF after Isha to save screen lifespan and power.

---

## 8. Milestone Roadmap

```
  Phase 1: Core Engine          Phase 2: Admin UI             Phase 3: Multi-Display & Themes
  +---------------------+       +---------------------+       +----------------------+
  | • FastAPI Service   |       | • Setup Wizard      |       | • Display grouping   |
  | • JAKIM Sync + DB   |  ---> | • Phone Admin UI    |  ---> | • Custom themes zip  |
  | • Basic Kiosk View  |       | • Carousel Manager  |       | • CEC Power Control  |
  +---------------------+       +---------------------+       +----------------------+
```

* **Phase 1 (MVP):** Core FastAPI application, JAKIM API integration, offline database engine, simple display screen with Adhan/Iqomah countdown and automatic screen dimming.
* **Phase 2 (Admin & Content):** Mobile-friendly Admin dashboard, local QR connection setup, carousel manager, and theme engine.
* **Phase 3 (Multi-Display & Hardware Polish):** Multi-screen grouping support, TV HDMI-CEC power toggling, community theme library, and automated Debian installer script.
