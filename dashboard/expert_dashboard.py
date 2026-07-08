"""
dashboard/expert_dashboard.py

Streamlit Expert Dashboard for Krishi Kalyan.

Features:
  - Auto-refreshes every 5 seconds (st.rerun loop).
  - Reads expert_requests Firestore collection, newest first.
  - Displays all ticket fields including weather, soil, satellite, farm_snapshot.
  - Shows uploaded images (inline) and voice audio players.
  - Per-ticket reply textarea + Send Reply button.
  - On Send Reply:
      • Calls POST /expert/reply (FastAPI backend) which:
          – Updates Firestore (status=Resolved, expert_reply, resolved_at)
          – Sends Telegram message to farmer

Run:
  streamlit run dashboard/expert_dashboard.py

Environment variables required:
  BACKEND_URL          – FastAPI backend base URL  (default: http://localhost:8000)
  STREAMLIT_REFRESH_SECONDS – auto-refresh interval (default: 5)
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Firebase / Firestore — reuse the same client the app uses
# ---------------------------------------------------------------------------
# We import the same firebase app so there is only one Firestore connection.
try:
    from app.database.firebase import db as firestore_client
    FIRESTORE_AVAILABLE = True
    print("✅ Firestore imported successfully")
except Exception as e:
    print("❌ FIREBASE IMPORT ERROR")
    print(e)
    import traceback
    traceback.print_exc()

    FIRESTORE_AVAILABLE = False
    firestore_client = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:8000")
COLLECTION: str = "expert_requests"
REFRESH_SECONDS: int = int(os.getenv("STREAMLIT_REFRESH_SECONDS", "5"))


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------

def _load_tickets() -> List[Dict[str, Any]]:
    """Load all expert tickets from Firestore, newest first."""
    if not FIRESTORE_AVAILABLE or firestore_client is None:
        logger.error("Firestore client not available")
        return []

    try:
        docs = (
            firestore_client
            .collection(COLLECTION)
            .order_by("created_at", direction="DESCENDING")
            .get()
        )
        tickets = []
        for doc in docs:
            data = doc.to_dict() or {}
            data["_doc_id"] = doc.id
            tickets.append(data)
        logger.info("Dashboard loaded %d tickets from Firestore", len(tickets))
        return tickets
    except Exception as exc:
        logger.exception("Failed to load tickets from Firestore: %s", exc)
        st.error(f"❌ Firestore error: {exc}")
        return []


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _send_reply(ticket_id: str, reply_text: str) -> bool:
    """POST the expert reply to the FastAPI backend."""
    url = f"{BACKEND_URL}/expert/reply"
    payload = {
        "ticket_id": ticket_id,
        "expert_reply": reply_text,
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        data = response.json()
        if data.get("success"):
            logger.info(
                "Reply sent successfully ticket_id=%s telegram=%s",
                ticket_id,
                data.get("telegram_sent"),
            )
            return True
        else:
            logger.error(
                "Backend returned failure ticket_id=%s error=%s",
                ticket_id,
                data.get("error"),
            )
            st.error(f"Backend error: {data.get('error')}")
            return False
    except requests.exceptions.RequestException as exc:
        logger.exception("HTTP request to backend failed: %s", exc)
        st.error(f"❌ Could not reach backend: {exc}")
        return False


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

_STATUS_COLOURS = {
    "Pending": "🟡",
    "In Progress": "🔵",
    "Resolved": "🟢",
}

_PRIORITY_COLOURS = {
    "Low": "⬇️",
    "Normal": "➡️",
    "High": "⬆️",
    "Critical": "🚨",
}


def _format_ts(ts: Any) -> str:
    """Convert a Firestore Timestamp or datetime to a readable string."""
    if ts is None:
        return "—"
    # Firestore Timestamps have a .isoformat() via datetime
    if hasattr(ts, "isoformat"):
        return ts.isoformat(sep=" ", timespec="seconds")
    return str(ts)


def _render_map_section(title: str, data: Optional[Dict[str, Any]]) -> None:
    """Render a collapsible expander for a nested map field."""
    if not data:
        st.markdown(f"**{title}:** —")
        return
    with st.expander(title, expanded=False):
        for k, v in data.items():
            st.markdown(f"- **{k}:** {v}")


def _render_ticket(ticket: Dict[str, Any], index: int) -> None:
    """Render a single expert ticket card."""
    ticket_id: str = ticket.get("ticket_id", "Unknown")
    status: str = ticket.get("status", "Pending")
    priority: str = ticket.get("priority", "Normal")
    status_icon = _STATUS_COLOURS.get(status, "⚪")
    priority_icon = _PRIORITY_COLOURS.get(priority, "➡️")

    with st.container():
        st.markdown("---")

        # ----- Header row -----
        col1, col2, col3 = st.columns([3, 2, 2])
        with col1:
            st.subheader(f"🎫 {ticket_id}")
        with col2:
            st.markdown(f"**Status:** {status_icon} {status}")
        with col3:
            st.markdown(f"**Priority:** {priority_icon} {priority}")

        # ----- Farmer info -----
        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a:
            st.markdown(f"**Farmer:** {ticket.get('farmer_name') or '—'}")
        with col_b:
            st.markdown(f"**Crop:** {ticket.get('crop') or '—'}")
        with col_c:
            st.markdown(f"**District:** {ticket.get('district') or '—'}")
        with col_d:
            st.markdown(f"**Village:** {ticket.get('village') or '—'}")

        col_e, col_f = st.columns(2)
        with col_e:
            st.markdown(f"**Season:** {ticket.get('season') or '—'}")
        with col_f:
            st.markdown(f"**Phone:** {ticket.get('phone') or '—'}")

        # ----- Timestamps -----
        col_t1, col_t2, col_t3 = st.columns(3)
        with col_t1:
            st.markdown(f"**Created:** {_format_ts(ticket.get('created_at'))}")
        with col_t2:
            st.markdown(f"**Updated:** {_format_ts(ticket.get('updated_at'))}")
        with col_t3:
            st.markdown(f"**Resolved:** {_format_ts(ticket.get('resolved_at'))}")

        st.markdown("#### 📋 Farmer Question")
        st.info(ticket.get("question") or "—")

        st.markdown("#### 🤖 AI Summary")
        st.warning(ticket.get("ai_summary") or "—")

        st.markdown("#### 💬 AI Recommendation (full response)")
        with st.expander("View full AI response", expanded=False):
            st.text(ticket.get("ai_response") or "—")

        # ----- Environment data -----
        env_col1, env_col2, env_col3 = st.columns(3)
        with env_col1:
            _render_map_section("🌤️ Weather", ticket.get("weather"))
        with env_col2:
            _render_map_section("🌱 Soil", ticket.get("soil"))
        with env_col3:
            _render_map_section("🛰️ Satellite", ticket.get("satellite"))

        _render_map_section("🚜 Farm Snapshot", ticket.get("farm_snapshot"))

        # ----- Media -----
        image_path: str = ticket.get("image_path") or ""
        voice_path: str = ticket.get("voice_path") or ""
        voice_transcript: str = ticket.get("voice_transcript") or ""

        if image_path and os.path.exists(image_path):
            st.markdown("#### 📸 Uploaded Image")
            st.image(image_path, use_column_width=True)
        elif image_path:
            st.markdown(f"**Image path (not on this server):** `{image_path}`")

        if voice_path and os.path.exists(voice_path):
            st.markdown("#### 🎙️ Voice Recording")
            with open(voice_path, "rb") as audio_file:
                st.audio(audio_file.read(), format="audio/wav")
        elif voice_path:
            st.markdown(f"**Voice path (not on this server):** `{voice_path}`")

        if voice_transcript:
            st.markdown("#### 📝 Voice Transcript")
            st.text(voice_transcript)

        # ----- Expert Reply (if already resolved) -----
        if status == "Resolved" and ticket.get("expert_reply"):
            st.markdown("#### ✅ Expert Reply (already sent)")
            st.success(ticket.get("expert_reply"))
            return  # No reply box needed

        # ----- Reply box -----
        st.markdown("#### 👨‍🌾 Send Expert Reply")

        reply_key = f"reply_{ticket_id}_{index}"
        reply_text = st.text_area(
            label="Type your expert reply here",
            key=reply_key,
            height=120,
            placeholder="e.g. Based on the symptoms, this appears to be powdery mildew. Apply sulfur-based fungicide...",
        )

        send_key = f"send_{ticket_id}_{index}"
        if st.button("📤 Send Reply", key=send_key, type="primary"):
            if not reply_text.strip():
                st.warning("⚠️ Please type a reply before sending.")
            else:
                with st.spinner("Sending reply to farmer..."):
                    success = _send_reply(
                        ticket_id=ticket_id,
                        reply_text=reply_text.strip(),
                    )
                if success:
                    st.success(
                        f"✅ Reply sent to farmer and ticket {ticket_id} marked as Resolved."
                    )
                    logger.info("Reply sent ticket_id=%s", ticket_id)
                    time.sleep(1)
                    st.rerun()


# ---------------------------------------------------------------------------
# Main dashboard layout
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Krishi Kalyan — Expert Dashboard",
        page_icon="🌾",
        layout="wide",
    )

    st.title("🌾 Krishi Kalyan — Agricultural Expert Dashboard")
    st.caption(f"Auto-refreshes every {REFRESH_SECONDS} seconds  •  Backend: {BACKEND_URL}")

    if not FIRESTORE_AVAILABLE:
        st.error(
            "❌ Firestore is not available. "
            "Ensure GOOGLE_APPLICATION_CREDENTIALS is set correctly "
            "and the Firebase app initialises without errors."
        )
        st.stop()

    # ----- Load tickets -----
    with st.spinner("Loading tickets from Firestore…"):
        tickets = _load_tickets()

    # ----- Summary metrics -----
    total = len(tickets)
    pending = sum(1 for t in tickets if t.get("status") == "Pending")
    resolved = sum(1 for t in tickets if t.get("status") == "Resolved")

    m1, m2, m3 = st.columns(3)
    m1.metric("📋 Total Tickets", total)
    m2.metric("🟡 Pending", pending)
    m3.metric("🟢 Resolved", resolved)

    if not tickets:
        st.info("No expert tickets yet. Farmers will appear here once they press 'Send to Expert'.")
    else:
        for i, ticket in enumerate(tickets):
            _render_ticket(ticket, i)

    # ----- Auto-refresh -----
    st.markdown(f"---\n*Page will auto-refresh in {REFRESH_SECONDS}s*")
    time.sleep(REFRESH_SECONDS)
    st.rerun()


if __name__ == "__main__":
    main()
