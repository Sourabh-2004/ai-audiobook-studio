import os
import sys
from pathlib import Path

# Add project root directory to python path for local imports
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import re
import time
import httpx
import streamlit as st
from src.config import BACKEND_URL, PROJECTS_DIR, SUPPORTED_MODELS, SUPPORTED_VOICES

# Page Configuration
st.set_page_config(
    page_title="AI Novel Condenser & Audiobook Generator",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling for a Premium Dark UI
st.markdown("""
<style>
    /* Dark Theme General Overrides */
    .stApp {
        background-color: #0f111a;
        color: #e2e8f0;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    
    /* Custom headers styling */
    h1, h2, h3, h4, h5, h6 {
        color: #ffffff !important;
        font-weight: 700 !important;
    }
    
    /* Glowing card containers (Glassmorphism) */
    .glass-card {
        background: rgba(30, 41, 59, 0.5);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 4px 20px 0 rgba(0, 0, 0, 0.2);
    }
    
    .stats-card {
        background: linear-gradient(135deg, rgba(37, 99, 235, 0.15) 0%, rgba(30, 41, 59, 0.5) 100%);
        border: 1px solid rgba(37, 99, 235, 0.25);
        border-radius: 12px;
        padding: 20px;
        text-align: center;
    }
    
    .stats-value {
        font-size: 2.2rem;
        font-weight: 800;
        color: #3b82f6;
        margin-bottom: 4px;
    }
    
    .stats-label {
        font-size: 0.9rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    
    /* Buttons Customization */
    .stButton>button {
        border-radius: 8px !important;
        font-weight: 600 !important;
        transition: all 0.2s ease-in-out !important;
    }
    
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3) !important;
    }
    
    /* Custom Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #0b0d14 !important;
        border-right: 1px solid rgba(255, 255, 255, 0.05) !important;
    }
    
    /* Custom info boxes */
    .stAlert {
        border-radius: 10px !important;
        border: 1px solid rgba(255, 255, 255, 0.05) !important;
    }
    
    /* Scrape result card */
    .scrape-preview {
        background: linear-gradient(135deg, rgba(16, 185, 129, 0.10) 0%, rgba(30, 41, 59, 0.5) 100%);
        border: 1px solid rgba(16, 185, 129, 0.25);
        border-radius: 12px;
        padding: 20px;
        margin: 15px 0;
    }
</style>
""", unsafe_allow_html=True)

# Helper function to call backend APIs
def api_request(method: str, path: str, json_data: dict = None, params: dict = None, raw_response: bool = False):
    try:
        url = f"{BACKEND_URL}{path}"
        with httpx.Client(timeout=300.0) as client:
            if method.lower() == "get":
                response = client.get(url, params=params)
            elif method.lower() == "post":
                response = client.post(url, json=json_data, params=params)
            elif method.lower() == "put":
                response = client.put(url, json=json_data)
            elif method.lower() == "delete":
                response = client.delete(url)
            else:
                raise ValueError(f"Unsupported method: {method}")
                
            if raw_response:
                return response
                
            if response.status_code in [200, 201]:
                return response.json()
            else:
                st.error(f"Backend returned error ({response.status_code}): {response.text}")
                return None
    except Exception as e:
        st.error(f"Failed to connect to backend server at {BACKEND_URL}. Is FastAPI running? Error: {str(e)}")
        return None

# --- Main App Logic ---

# Initialize Session States
if "selected_project_id" not in st.session_state:
    st.session_state.selected_project_id = None
if "reviewing_chapter_id" not in st.session_state:
    st.session_state.reviewing_chapter_id = None
if "editing_chapter_id" not in st.session_state:
    st.session_state.editing_chapter_id = None
if "scrape_preview_data" not in st.session_state:
    st.session_state.scrape_preview_data = None

# Sidebar Content
with st.sidebar:
    st.image("https://img.icons8.com/nolan/96/audio-book.png", width=64)
    st.title("Novel Condenser")
    st.markdown("<p style='color: #64748b; font-size: 0.85rem; margin-top: -15px;'>Local Offline AI Studio</p>", unsafe_allow_html=True)
    st.markdown("---")
    
    # Check Ollama backend status
    try:
        ollama_status_resp = httpx.get(f"{BACKEND_URL}/project/1", timeout=1.0)  # Dummy call or just check port
        fastapi_alive = True
    except Exception:
        fastapi_alive = False
        
    if fastapi_alive:
        st.success("⚡ System Core API Active")
    else:
        st.error("❌ System Core Offline. Check logs.")
        
    # Project Creator Form
    with st.expander("➕ Create New Project", expanded=False):
        new_project_name = st.text_input("Project Name", placeholder="e.g. Harry Potter Book 1")
        
        # Settings for project
        model_opt = st.selectbox("AI Condense Model", list(SUPPORTED_MODELS.keys()), format_func=lambda x: SUPPORTED_MODELS[x])
        temp = st.slider("Temperature", 0.1, 1.0, 0.5, 0.1)
        voice_opt = st.selectbox("Narrator Voice", SUPPORTED_VOICES)
        speed = st.slider("Narrator Speed", 0.5, 2.0, 1.0, 0.1)
        audio_format = st.selectbox("Audio Output Format", ["wav", "mp3"])
        
        if st.button("Create Project", use_container_width=True):
            if new_project_name.strip():
                # Clean name for folders
                clean_name = re.sub(r'[\\/*?:"<>|]', "", new_project_name.strip())
                payload = {
                    "name": clean_name,
                    "model_name": model_opt,
                    "temperature": temp,
                    "max_tokens": 4096,
                    "tts_voice": voice_opt,
                    "tts_speed": speed,
                    "tts_volume": 1.0,
                    "audio_format": audio_format
                }
                result = api_request("POST", "/project", json_data=payload)
                if result:
                    st.success(f"Project '{clean_name}' created!")
                    st.session_state.selected_project_id = result["id"]
                    st.rerun()
            else:
                st.warning("Please enter a valid project name.")

    # Project Switcher
    st.markdown("### Projects Directory")
    projects_list = api_request("GET", "/projects")
    if projects_list:
        project_names = [p["name"] for p in projects_list]
        project_ids = [p["id"] for p in projects_list]
        
        current_project_index = 0
        if st.session_state.selected_project_id in project_ids:
            current_project_index = project_ids.index(st.session_state.selected_project_id) + 1
            
        options = ["-- Select Project / Home --"] + project_names
        selected_option = st.selectbox("Active Workspace", options, index=current_project_index)
        
        if selected_option == "-- Select Project / Home --":
            st.session_state.selected_project_id = None
        else:
            proj_idx = project_names.index(selected_option)
            st.session_state.selected_project_id = project_ids[proj_idx]
    else:
        st.info("No projects found. Create one above!")

    st.markdown("---")
    # Quick help
    st.markdown("""
    💡 **Workflow:**
    1. Select or create a project.
    2. Upload, paste, or **scrape** a chapter.
    3. Run AI Condensation (→ 50%).
    4. Review & edit side-by-side.
    5. Generate audio & audiobook.
    """)

# --- MAIN SCREEN LOGIC ---

if st.session_state.selected_project_id is None:
    # --- DASHBOARD HOME ---
    st.markdown("# Novel Condenser Dashboard")
    st.markdown("Create condensed books and compile high-fidelity audiobooks offline using local AI.")
    
    # Global Stats
    projects = api_request("GET", "/projects") or []
    total_projects = len(projects)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"""
        <div class="stats-card">
            <div class="stats-value">{total_projects}</div>
            <div class="stats-label">Active Projects</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        # Sum of chapters across all projects
        total_ch = 0
        for p in projects:
            p_details = api_request("GET", f"/project/{p['id']}")
            if p_details:
                total_ch += len(p_details.get("chapters", []))
        st.markdown(f"""
        <div class="stats-card">
            <div class="stats-value">{total_ch}</div>
            <div class="stats-label">Total Chapters Processed</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="stats-card">
            <div class="stats-value">100%</div>
            <div class="stats-label">Offline Integrity</div>
        </div>
        """, unsafe_allow_html=True)
        
    st.markdown("### Recent Projects")
    if projects:
        search_query = st.text_input("🔍 Search Projects", "")
        
        filtered_projects = [
            p for p in projects if search_query.lower() in p["name"].lower()
        ]
        
        for proj in filtered_projects:
            proj_details = api_request("GET", f"/project/{proj['id']}")
            stats = proj_details.get("statistics", {}) if proj_details else {}
            
            with st.container():
                st.markdown(f"""
                <div class="glass-card">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <h3 style="margin: 0; padding: 0;">📘 {proj['name']}</h3>
                            <span style="color: #64748b; font-size: 0.85rem;">Last Updated: {proj['updated_at'][:10]}</span>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Render Streamlit buttons for actions inside container columns
                c1, c2, c3, c4 = st.columns([2, 2, 2, 4])
                with c1:
                    st.write(f"📂 {stats.get('chapters_count', 0)} Chapters")
                with c2:
                    st.write(f"📝 {stats.get('words_condensed', 0)} Words")
                with c3:
                    if st.button("Open Project", key=f"open_{proj['id']}", use_container_width=True):
                        st.session_state.selected_project_id = proj["id"]
                        st.rerun()
                with c4:
                    if st.button("🗑️ Delete", key=f"del_{proj['id']}"):
                        # Direct confirmation or just delete (for speed)
                        api_request("DELETE", f"/project/{proj['id']}")
                        st.success(f"Project '{proj['name']}' deleted.")
                        st.rerun()
    else:
        st.info("No projects created yet. Use the sidebar form to make your first project!")

else:
    # --- ACTIVE PROJECT WORKSPACE ---
    project_id = st.session_state.selected_project_id
    project = api_request("GET", f"/project/{project_id}")
    
    if not project:
        st.session_state.selected_project_id = None
        st.rerun()
        
    stats = project.get("statistics", {})
    chapters = project.get("chapters", [])
    jobs = project.get("jobs", [])
    
    st.markdown(f"# 📘 {project['name']}")
    
    # Mini stats row
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Chapters", stats.get("chapters_count", 0))
    with col2:
        st.metric("Original Words", stats.get("words_original", 0))
    with col3:
        st.metric("Condensed Words", stats.get("words_condensed", 0))
    with col4:
        st.metric("Words Saved", f"{stats.get('compression_percentage', 0.0)}%")
    with col5:
        # Convert seconds to standard duration string
        dur_secs = stats.get("audio_duration_seconds", 0.0)
        mins, secs = divmod(int(dur_secs), 60)
        hrs, mins = divmod(mins, 60)
        st.metric("Est. Audio Duration", f"{hrs:02d}:{mins:02d}:{secs:02d}")

    active_jobs = [j for j in jobs if j["status"] in ["pending", "running"]]
    has_active_jobs = len(active_jobs) > 0
    if active_jobs:
        st.markdown("### ⚙️ System Processing Tasks")
        for job in active_jobs:
            ch_name = ""
            if job["chapter_id"]:
                ch = next((c for c in chapters if c["id"] == job["chapter_id"]), None)
                if ch:
                    ch_name = f" - '{ch['title']}'"
            
            task_desc = f"**{job['job_type'].upper()}**{ch_name} (Status: {job['status']})"
            st.write(task_desc)
            st.progress(job["progress"] / 100.0)
            # Add small note if downloading or processing
            if job.get("error_message"):
                st.info(job["error_message"])

    # Check if selected Ollama model is downloaded
    ollama_data = api_request("GET", "/ollama/models")
    if ollama_data is not None:
        installed_models = ollama_data.get("models", [])
        selected_model = project["model_name"]
        
        # Check if model is downloaded (exact match or same base name)
        model_is_installed = False
        for m in installed_models:
            if m == selected_model or m.split(":")[0] == selected_model.split(":")[0]:
                model_is_installed = True
                break
                
        if not model_is_installed:
            st.warning(f"⚠️ Selected AI model '{selected_model}' is not downloaded in your local Ollama registry.")
            col_pull, _ = st.columns([3, 7])
            with col_pull:
                if st.button(f"📥 Download '{selected_model}' via Ollama", use_container_width=True):
                    api_request("POST", f"/project/{project_id}/pull-model")
                    st.toast("Triggered Ollama pull request...")
                    st.rerun()

    # Tabs for project interface
    tab_workspace, tab_add, tab_preview, tab_audiobook, tab_settings = st.tabs([
        "📁 Chapters Workspace", 
        "➕ Add Chapter", 
        "👁️ Preview Book", 
        "🔊 Audiobook Generator",
        "⚙️ Workspace Settings"
    ])

    with tab_workspace:
        st.subheader("Book Chapters")
        
        if not chapters:
            st.info("Your project folder is empty. Go to 'Add Chapter' to start uploading.")
        else:
            # Search chapter bar
            ch_search = st.text_input("🔍 Search Chapter List", "")
            
            for idx, ch in enumerate(chapters):
                if ch_search and ch_search.lower() not in ch["title"].lower():
                    continue
                    
                with st.container():
                    st.markdown(f"""
                    <div style="border-left: 5px solid { '#10b981' if ch['status'] == 'completed' else '#3b82f6' if ch['status'] == 'condensed' else '#eab308' if ch['status'] in ['condensing', 'generating_audio'] else '#64748b' }; padding-left: 15px; margin-bottom: 15px;" class="glass-card">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                            <div>
                                <h4 style="margin: 0; padding: 0;">Chapter {ch['order_index']}: {ch['title']}</h4>
                                <span style="font-size: 0.8rem; color: #94a3b8;">Original: {ch['word_count_original']} words | Condensed: {ch['word_count_condensed']} words</span>
                            </div>
                            <div>
                                <span style="background-color: rgba(255,255,255,0.05); padding: 4px 10px; border-radius: 20px; font-size: 0.8rem; font-weight: 600;">Status: {ch['status'].upper()}</span>
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Buttons Row
                    col_b1, col_b2, col_b3, col_b4, col_b5, col_b6 = st.columns([1.5, 1.5, 1.5, 1, 1, 2.5])
                    
                    with col_b1:
                        # Condensation action
                        if ch["status"] == "draft":
                            if st.button("Shorten Chapter", key=f"cond_{ch['id']}", use_container_width=True):
                                api_request("POST", f"/chapter/{ch['id']}/condense")
                                st.rerun()
                        elif ch["status"] == "condensing":
                            st.button("Condensing...", disabled=True, key=f"cond_p_{ch['id']}", use_container_width=True)
                        else:
                            if st.button("Re-Condense", key=f"recond_{ch['id']}", use_container_width=True):
                                api_request("POST", f"/chapter/{ch['id']}/condense")
                                st.rerun()
                                
                    with col_b2:
                        # Review action
                        if ch["condensed_text"]:
                            if st.button("🔍 Review & Edit", key=f"rev_{ch['id']}", use_container_width=True):
                                st.session_state.reviewing_chapter_id = ch["id"]
                                st.rerun()
                        else:
                            st.button("Review", disabled=True, key=f"rev_dis_{ch['id']}", use_container_width=True)
                            
                    with col_b3:
                        # TTS action
                        if ch["status"] == "condensed":
                            if st.button("Generate Voice", key=f"tts_{ch['id']}", use_container_width=True):
                                api_request("POST", f"/chapter/{ch['id']}/tts")
                                st.rerun()
                        elif ch["status"] == "generating_audio":
                            st.button("Generating...", disabled=True, key=f"tts_p_{ch['id']}", use_container_width=True)
                        elif ch["status"] == "completed":
                            if st.button("Regen Voice", key=f"tts_re_{ch['id']}", use_container_width=True):
                                api_request("POST", f"/chapter/{ch['id']}/tts")
                                st.rerun()
                        else:
                            st.button("Generate Voice", disabled=True, key=f"tts_dis_{ch['id']}", use_container_width=True)
                            
                    with col_b4:
                        # Duplicate Chapter
                        if st.button("👯", help="Duplicate Chapter", key=f"dup_{ch['id']}", use_container_width=True):
                            # Create chapter with exact same text
                            dup_title = f"{ch['title']} (Copy)"
                            payload = {"title": dup_title, "original_text": ch["original_text"]}
                            new_ch = api_request("POST", f"/project/{project_id}/chapter", json_data=payload)
                            if new_ch and ch["condensed_text"]:
                                # Copy condensed text if exists
                                api_request("PUT", f"/chapter/{new_ch['id']}", json_data={"condensed_text": ch["condensed_text"]})
                            st.rerun()
                            
                    with col_b5:
                        # Delete Chapter
                        if st.button("🗑️", help="Delete Chapter", key=f"del_ch_{ch['id']}", use_container_width=True):
                            api_request("DELETE", f"/chapter/{ch['id']}")
                            st.rerun()

                    with col_b6:
                        # Reordering (Move Up / Move Down)
                        c_up, c_down = st.columns(2)
                        with c_up:
                            if idx > 0:
                                if st.button("⬆️ Up", key=f"up_{ch['id']}", use_container_width=True):
                                    api_name = f"/chapter/{ch['id']}"
                                    api_request("PUT", api_name, json_data={"order_index": ch["order_index"] - 1})
                                    st.rerun()
                        with c_down:
                            if idx < len(chapters) - 1:
                                if st.button("⬇️ Down", key=f"down_{ch['id']}", use_container_width=True):
                                    api_name = f"/chapter/{ch['id']}"
                                    api_request("PUT", api_name, json_data={"order_index": ch["order_index"] + 1})
                                    st.rerun()

            # Merge and Split Tools
            st.markdown("---")
            st.subheader("Advanced Chapter Editing Tools")
            col_tool1, col_tool2 = st.columns(2)
            
            with col_tool1:
                with st.expander("🔗 Merge Chapters", expanded=False):
                    st.write("Merge two contiguous chapters into a single chapter.")
                    ch_titles = [f"Ch {c['order_index']}: {c['title']}" for c in chapters]
                    
                    merge_ch1 = st.selectbox("Select First Chapter", ch_titles, key="merge_ch1")
                    merge_ch2 = st.selectbox("Select Second Chapter", ch_titles, key="merge_ch2")
                    
                    idx1 = ch_titles.index(merge_ch1)
                    idx2 = ch_titles.index(merge_ch2)
                    
                    if st.button("Merge Now", use_container_width=True):
                        if abs(idx1 - idx2) != 1:
                            st.error("You can only merge adjacent chapters (e.g. Chapter 1 and Chapter 2).")
                        else:
                            # Order them
                            first_idx = min(idx1, idx2)
                            second_idx = max(idx1, idx2)
                            ch1 = chapters[first_idx]
                            ch2 = chapters[second_idx]
                            
                            # Merge content
                            merged_orig = f"{ch1['original_text']}\n\n{ch2['original_text']}"
                            merged_cond = ""
                            if ch1["condensed_text"] and ch2["condensed_text"]:
                                merged_cond = f"{ch1['condensed_text']}\n\n{ch2['condensed_text']}"
                                
                            merged_title = f"{ch1['title']} + {ch2['title']}"
                            
                            # Create new merged chapter
                            payload = {"title": merged_title, "original_text": merged_orig}
                            new_merged = api_request("POST", f"/project/{project_id}/chapter", json_data=payload)
                            
                            if new_merged:
                                # Update order index to sit in the place of the first chapter
                                api_request("PUT", f"/chapter/{new_merged['id']}", json_data={
                                    "order_index": ch1["order_index"],
                                    "condensed_text": merged_cond if merged_cond else None
                                })
                                # Delete old chapters
                                api_request("DELETE", f"/chapter/{ch1['id']}")
                                api_request("DELETE", f"/chapter/{ch2['id']}")
                                
                                st.success("Chapters merged successfully!")
                                st.rerun()
                                
            with col_tool2:
                with st.expander("✂️ Split Chapter", expanded=False):
                    st.write("Split a chapter into two at a custom separator string or marker.")
                    split_ch_opt = st.selectbox("Select Chapter to Split", ch_titles, key="split_ch")
                    split_idx = ch_titles.index(split_ch_opt)
                    target_ch = chapters[split_idx]
                    
                    split_marker = st.text_input("Split Marker Text", placeholder="e.g. *** or CHAPTER TWO or any sentence")
                    
                    if st.button("Split Now", use_container_width=True):
                        if not split_marker.strip():
                            st.warning("Please specify a split marker.")
                        elif split_marker not in target_ch["original_text"]:
                            st.error("Split marker not found inside the chapter's original text.")
                        else:
                            # Perform split
                            orig_parts = target_ch["original_text"].split(split_marker, 1)
                            part1_orig = orig_parts[0].strip()
                            part2_orig = (split_marker + orig_parts[1]).strip()  # Keep marker in the second part
                            
                            # Split condensed text if present
                            part1_cond = ""
                            part2_cond = ""
                            if target_ch["condensed_text"]:
                                # Try splitting condensed text by marker, or just do it by character percentage estimate
                                if split_marker in target_ch["condensed_text"]:
                                    cond_parts = target_ch["condensed_text"].split(split_marker, 1)
                                    part1_cond = cond_parts[0].strip()
                                    part2_cond = (split_marker + cond_parts[1]).strip()
                                else:
                                    # Fallback: estimate split ratio
                                    ratio = len(part1_orig) / len(target_ch["original_text"])
                                    split_char_idx = int(len(target_ch["condensed_text"]) * ratio)
                                    part1_cond = target_ch["condensed_text"][:split_char_idx].strip()
                                    part2_cond = target_ch["condensed_text"][split_char_idx:].strip()

                            # Create second part
                            payload = {"title": f"{target_ch['title']} (Part 2)", "original_text": part2_orig}
                            ch_part2 = api_request("POST", f"/project/{project_id}/chapter", json_data=payload)
                            
                            if ch_part2:
                                # Set correct order index for part 2
                                api_request("PUT", f"/chapter/{ch_part2['id']}", json_data={
                                    "order_index": target_ch["order_index"] + 1,
                                    "condensed_text": part2_cond if part2_cond else None
                                })
                                # Update part 1 (original chapter)
                                api_request("PUT", f"/chapter/{target_ch['id']}", json_data={
                                    "title": f"{target_ch['title']} (Part 1)",
                                    "original_text": part1_orig,
                                    "condensed_text": part1_cond if part1_cond else None
                                })
                                
                                st.success("Chapter split successfully into Part 1 and Part 2!")
                                st.rerun()

    # Render Review Modal Overlay in-place if active
    if st.session_state.reviewing_chapter_id:
        st.markdown("---")
        rev_id = st.session_state.reviewing_chapter_id
        rev_ch = next((c for c in chapters if c["id"] == rev_id), None)
        
        if rev_ch:
            st.markdown(f"### 🔍 Editing / Review Workspace: **{rev_ch['title']}**")
            
            # Show word reduction percentage
            orig_wc = rev_ch["word_count_original"]
            cond_wc = rev_ch["word_count_condensed"]
            reduction = round((1 - (cond_wc / orig_wc)) * 100, 1) if orig_wc > 0 else 0
            
            st.info(f"Word Reduction: **{reduction}%** (Original: {orig_wc} words | Condensed: {cond_wc} words)")
            
            col_rev1, col_rev2 = st.columns(2)
            with col_rev1:
                st.subheader("Original Chapter Content")
                st.text_area("Original (Read Only)", rev_ch["original_text"], height=400, disabled=True, key="rev_orig_text")
                
            with col_rev2:
                st.subheader("Condensed Chapter Content (Editable)")
                # Editable text area for manual refinement
                edited_condensed = st.text_area("Review and refine text here...", rev_ch["condensed_text"] or "", height=400, key="rev_cond_text")
                
            # Review Screen Buttons
            col_act1, col_act2, col_act3, col_act4 = st.columns(4)
            with col_act1:
                if st.button("💾 Save & Accept Changes", type="primary", use_container_width=True):
                    # Save edited content and update status to condensed
                    api_request("PUT", f"/chapter/{rev_id}", json_data={
                        "condensed_text": edited_condensed,
                        "status": "condensed"
                    })
                    st.toast("Chapter updates accepted and saved.")
                    st.session_state.reviewing_chapter_id = None
                    st.rerun()
            with col_act2:
                if st.button("🔄 Regenerate with AI", use_container_width=True):
                    # Trigger Ollama condensation again
                    api_request("POST", f"/chapter/{rev_id}/condense")
                    st.toast("Triggered AI regeneration...")
                    st.session_state.reviewing_chapter_id = None
                    st.rerun()
            with col_act3:
                if st.button("❌ Discard Draft", use_container_width=True):
                    # Delete the condensed draft, reverting status to draft
                    api_request("PUT", f"/chapter/{rev_id}", json_data={
                        "condensed_text": "",
                        "status": "draft"
                    })
                    st.toast("Condensed draft discarded.")
                    st.session_state.reviewing_chapter_id = None
                    st.rerun()
            with col_act4:
                if st.button("Close Workspace", use_container_width=True):
                    st.session_state.reviewing_chapter_id = None
                    st.rerun()

    with tab_add:
        st.subheader("Add Novel Chapters")
        st.write("Extract texts from pasted values, TXT/PDF/EPUB files, or **scrape directly from the web**.")
        
        # Chapter upload configuration
        upload_mode = st.radio(
            "Input Method", 
            ["Paste Text", "Upload File (TXT, PDF, EPUB)", "🌐 Scrape from Web", "Batch Import Files"],
            horizontal=True
        )
        
        if upload_mode == "Paste Text":
            pasted_title = st.text_input("Chapter Title (Optional - will auto-detect if blank)")
            pasted_text = st.text_area("Paste Chapter Text", height=300)
            
            if st.button("Save Pasted Chapter", type="primary"):
                if pasted_text.strip():
                    payload = {
                        "title": pasted_title.strip() if pasted_title.strip() else None,
                        "original_text": pasted_text.strip()
                    }
                    result = api_request("POST", f"/project/{project_id}/chapter", json_data=payload)
                    if result:
                        st.success(f"Chapter '{result['title']}' saved successfully!")
                        st.rerun()
                else:
                    st.warning("Please paste some text before saving.")
                    
        elif upload_mode == "Upload File (TXT, PDF, EPUB)":
            uploaded_file = st.file_uploader("Select File", type=["txt", "pdf", "epub"])
            
            if uploaded_file is not None:
                # Save uploaded file temporarily to extract it
                temp_dir = PROJECTS_DIR.parent / "scratch"
                temp_dir.mkdir(exist_ok=True)
                temp_path = temp_dir / uploaded_file.name
                
                with open(temp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                    
                st.info("File uploaded. Analyzing content...")
                
                # Import extraction modules locally
                from src.document_utils import extract_text, auto_detect_chapter_title
                try:
                    text_content = extract_text(temp_path)
                    
                    st.success("Text successfully extracted!")
                    st.text_area("Preview Extracted Text (First 1000 characters)", text_content[:1000] + "...", height=150, disabled=True)
                    
                    custom_title = st.text_input("Confirm/Edit Chapter Title", auto_detect_chapter_title(text_content, uploaded_file.name.rsplit('.', 1)[0]))
                    
                    if st.button("Save Extracted Chapter", type="primary"):
                        payload = {
                            "title": custom_title,
                            "original_text": text_content
                        }
                        result = api_request("POST", f"/project/{project_id}/chapter", json_data=payload)
                        if result:
                            st.success(f"Chapter '{result['title']}' added to project!")
                            # Clean up
                            temp_path.unlink()
                            st.rerun()
                except Exception as e:
                    st.error(f"Error extracting file content: {str(e)}")
                    if temp_path.exists():
                        temp_path.unlink()

        elif upload_mode == "🌐 Scrape from Web":
            st.markdown("""
            <div class="glass-card">
                <h4 style="margin-top: 0;">🌐 Web Scraper</h4>
                <p style="color: #94a3b8; font-size: 0.9rem;">
                    Import novel chapters directly from websites. Supports <b>Royal Road</b>, 
                    <b>Wattpad</b>, <b>Archive of Our Own</b>, and any generic web page with readable content.
                </p>
            </div>
            """, unsafe_allow_html=True)
            
            scrape_mode = st.radio(
                "Scrape Mode",
                ["Single Chapter URL", "Bulk Import (Table of Contents)"],
                horizontal=True,
                key="scrape_mode_radio"
            )
            
            if scrape_mode == "Single Chapter URL":
                scrape_url = st.text_input(
                    "Chapter URL", 
                    placeholder="https://www.royalroad.com/fiction/12345/chapter/67890/chapter-title",
                    key="single_scrape_url"
                )
                
                col_preview, col_import = st.columns(2)
                
                with col_preview:
                    if st.button("👁️ Preview Content", use_container_width=True, disabled=not scrape_url.strip()):
                        if scrape_url.strip():
                            with st.spinner("Fetching page content..."):
                                preview_data = api_request("GET", "/scrape/preview", params={"url": scrape_url.strip()})
                                if preview_data:
                                    st.session_state.scrape_preview_data = preview_data
                
                with col_import:
                    if st.button("📥 Import as Chapter", type="primary", use_container_width=True, disabled=not scrape_url.strip()):
                        if scrape_url.strip():
                            result = api_request("POST", f"/project/{project_id}/scrape", json_data={"url": scrape_url.strip()})
                            if result:
                                st.success("Scrape job started! Check the processing panel for progress.")
                                st.session_state.scrape_preview_data = None
                                st.rerun()
                
                # Show preview if available
                if st.session_state.scrape_preview_data:
                    preview = st.session_state.scrape_preview_data
                    st.markdown(f"""
                    <div class="scrape-preview">
                        <h4 style="margin-top: 0; color: #10b981;">📄 {preview.get('title', 'Untitled')}</h4>
                        <div style="display: flex; gap: 20px; margin-bottom: 10px;">
                            <span style="color: #94a3b8;">📝 {preview.get('word_count', 0)} words</span>
                            <span style="color: #94a3b8;">🌐 {preview.get('source_url', '')[:60]}...</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    st.text_area(
                        "Content Preview (first 2000 characters)", 
                        preview.get("preview", "")[:2000] + "..." if len(preview.get("preview", "")) > 2000 else preview.get("preview", ""),
                        height=200, 
                        disabled=True,
                        key="scrape_preview_text"
                    )
                    
            else:  # Bulk Import
                bulk_url = st.text_input(
                    "Table of Contents URL",
                    placeholder="https://www.royalroad.com/fiction/12345/my-novel",
                    key="bulk_scrape_url"
                )
                
                col_bulk_settings1, col_bulk_settings2 = st.columns(2)
                with col_bulk_settings1:
                    max_chapters = st.number_input("Max Chapters to Import", min_value=1, max_value=200, value=50, key="bulk_max_ch")
                with col_bulk_settings2:
                    scrape_delay = st.slider("Delay Between Requests (seconds)", 0.5, 10.0, 1.5, 0.5, key="bulk_delay")
                
                st.info("💡 The scraper will detect chapter links on the page and import them sequentially. "
                        "A delay is applied between requests to be polite to the server.")
                
                if st.button("🚀 Start Bulk Import", type="primary", use_container_width=True, disabled=not bulk_url.strip()):
                    if bulk_url.strip():
                        result = api_request("POST", f"/project/{project_id}/scrape-bulk", json_data={
                            "url": bulk_url.strip(),
                            "max_chapters": max_chapters,
                            "delay": scrape_delay
                        })
                        if result:
                            st.success(f"Bulk scrape started! Importing up to {max_chapters} chapters. Check the processing panel for progress.")
                            st.rerun()
                    
        elif upload_mode == "Batch Import Files":
            st.write("Upload multiple chapter files at once. They will be sorted by file name and imported sequentially.")
            uploaded_files = st.file_uploader("Choose multiple files", type=["txt", "pdf", "epub"], accept_multiple_files=True)
            
            if uploaded_files:
                if st.button("Import Batch", type="primary"):
                    from src.document_utils import extract_text, auto_detect_chapter_title
                    temp_dir = PROJECTS_DIR.parent / "scratch"
                    temp_dir.mkdir(exist_ok=True)
                    
                    import_count = 0
                    progress_text = st.empty()
                    pbar = st.progress(0)
                    
                    for idx, uf in enumerate(sorted(uploaded_files, key=lambda f: f.name)):
                        progress_text.write(f"Importing {uf.name} ({idx+1}/{len(uploaded_files)})...")
                        
                        temp_path = temp_dir / uf.name
                        with open(temp_path, "wb") as f:
                            f.write(uf.getbuffer())
                            
                        try:
                            text_content = extract_text(temp_path)
                            title = auto_detect_chapter_title(text_content, uf.name.rsplit('.', 1)[0])
                            
                            payload = {"title": title, "original_text": text_content}
                            api_request("POST", f"/project/{project_id}/chapter", json_data=payload)
                            
                            import_count += 1
                        except Exception as e:
                            st.error(f"Failed to import {uf.name}: {str(e)}")
                        finally:
                            if temp_path.exists():
                                temp_path.unlink()
                                
                        pbar.progress((idx + 1) / len(uploaded_files))
                        
                    st.success(f"Successfully batch imported {import_count} chapters!")
                    time.sleep(2)
                    st.rerun()

    with tab_preview:
        st.subheader("Preview Condensed Novel")
        
        if not chapters:
            st.info("No chapters available to preview.")
        else:
            # Table of Contents sidebar-like navigation inside tab
            col_nav, col_read = st.columns([3, 7])
            
            # Simple global search & replace tool
            with col_nav:
                st.markdown("### Search & Replace")
                find_txt = st.text_input("Find text", "")
                replace_txt = st.text_input("Replace with", "")
                if st.button("Global Replace", use_container_width=True):
                    if find_txt.strip():
                        replaced_count = 0
                        for ch in chapters:
                            text = ch["condensed_text"] or ch["original_text"]
                            if find_txt in text:
                                new_text = text.replace(find_txt, replace_txt)
                                api_request("PUT", f"/chapter/{ch['id']}", json_data={"condensed_text": new_text})
                                replaced_count += 1
                        st.success(f"Replaced text in {replaced_count} chapters.")
                        st.rerun()
                        
                st.markdown("---")
                st.markdown("### Table of Contents")
                toc_indices = [f"Chapter {c['order_index']}: {c['title']}" for c in chapters]
                selected_toc = st.radio("Jump to Chapter", toc_indices)
                active_idx = toc_indices.index(selected_toc)
                active_ch = chapters[active_idx]
                
            with col_read:
                st.markdown(f"## {active_ch['title']}")
                st.markdown(f"<span style='color: #64748b; font-size: 0.8rem;'>Chapter Order: {active_ch['order_index']}</span>", unsafe_allow_html=True)
                
                # Render content
                # Make it an autosave textarea for live modifications during reading!
                content_to_render = active_ch["condensed_text"] or active_ch["original_text"]
                is_original = not active_ch["condensed_text"]
                
                if is_original:
                    st.warning("⚠️ Showing original text (Chapter has not been condensed yet).")
                    
                edited_reader_text = st.text_area(
                    "Reader Canvas (Edits are autosaved)", 
                    content_to_render, 
                    height=500, 
                    key=f"read_canvas_{active_ch['id']}"
                )
                
                # Simple Autosave mechanism
                if edited_reader_text != content_to_render:
                    # Save changes automatically
                    api_request("PUT", f"/chapter/{active_ch['id']}", json_data={
                        "condensed_text" if not is_original else "original_text": edited_reader_text
                    })
                    st.toast("Draft autosaved.")
                    
                # Exports buttons
                st.markdown("---")
                st.subheader("Export Workspace")
                col_ex1, col_ex2, col_ex3 = st.columns(3)
                
                with col_ex1:
                    # Export TXT
                    txt_url = f"{BACKEND_URL}/project/{project_id}/export/txt"
                    st.markdown(f'<a href="{txt_url}" target="_blank"><button style="width:100%; height:40px; background-color:#1e293b; color:white; border:none; border-radius:8px; font-weight:600; cursor:pointer;">📥 Download TXT</button></a>', unsafe_allow_html=True)
                with col_ex2:
                    # Export PDF
                    pdf_url = f"{BACKEND_URL}/project/{project_id}/export/pdf"
                    st.markdown(f'<a href="{pdf_url}" target="_blank"><button style="width:100%; height:40px; background-color:#1e293b; color:white; border:none; border-radius:8px; font-weight:600; cursor:pointer;">📥 Download PDF</button></a>', unsafe_allow_html=True)
                with col_ex3:
                    # Export DOCX
                    docx_url = f"{BACKEND_URL}/project/{project_id}/export/docx"
                    st.markdown(f'<a href="{docx_url}" target="_blank"><button style="width:100%; height:40px; background-color:#1e293b; color:white; border:none; border-radius:8px; font-weight:600; cursor:pointer;">📥 Download DOCX</button></a>', unsafe_allow_html=True)

    with tab_audiobook:
        st.subheader("Audiobook Builder")
        st.write("Generate individual voice tracks and compile a single audiobook output file.")
        
        # Verify if TTS model is downloaded
        from src.tts_client import TTSClient
        tts_checker = TTSClient()
        model_ready = tts_checker.is_model_downloaded()
        
        if not model_ready:
            st.warning("⚠️ TTS model files (approx. 120MB) are not present in your local 'models/' directory.")
            if st.button("📥 Download TTS Models from GitHub", type="primary"):
                # We trigger a dummy TTS job which auto-downloads the model files and shows progress
                # Find first chapter to generate
                if chapters:
                    api_request("POST", f"/chapter/{chapters[0]['id']}/tts")
                    st.rerun()
                else:
                    st.error("Add at least one chapter before downloading models.")
        else:
            st.success("✅ TTS models loaded and ready")
            
            # Voice test section
            with st.expander("🎤 Test Voice Preview", expanded=False):
                test_voice = st.selectbox("Select Voice to Test", SUPPORTED_VOICES, key="test_voice_select")
                test_speed = st.slider("Test Speed", 0.5, 2.0, 1.0, 0.1, key="test_voice_speed")
                if st.button("🔊 Play Test Audio", use_container_width=True):
                    test_url = f"{BACKEND_URL}/tts/test?voice={test_voice}&speed={test_speed}"
                    try:
                        with httpx.Client(timeout=30.0) as client:
                            resp = client.get(test_url)
                            if resp.status_code == 200:
                                st.audio(resp.content, format="audio/wav")
                            else:
                                st.error(f"Voice test failed: {resp.text}")
                    except Exception as e:
                        st.error(f"Voice test error: {str(e)}")
            
            # Audiobook compilation trigger
            st.markdown("### audiobook.mp3 Compiler")
            pause_time = st.slider("Pause between chapters (seconds)", 0.0, 5.0, 2.0, 0.5)
            
            # Check completed chapters
            completed_audio_chapters = [c for c in chapters if c["audio_path"] is not None]
            st.write(f"Audio tracks generated: **{len(completed_audio_chapters)} / {len(chapters)}** chapters.")
            
            col_audio1, col_audio2 = st.columns(2)
            with col_audio1:
                if st.button("🔊 Compile Audiobook File", type="primary", use_container_width=True, disabled=len(completed_audio_chapters) == 0):
                    api_request("POST", f"/project/{project_id}/audiobook", params={"pause_duration": pause_time})
                    st.success("Compilation started in the background.")
                    st.rerun()
            with col_audio2:
                # Download audiobook link if file exists
                proj_name = project["name"]
                exports_path = PROJECTS_DIR / proj_name / "exports"
                
                audiobook_mp3 = exports_path / "audiobook.mp3"
                audiobook_wav = exports_path / "audiobook.wav"
                
                audio_exists = audiobook_mp3.exists() or audiobook_wav.exists()
                
                if audio_exists:
                    dl_url = f"{BACKEND_URL}/project/{project_id}/audiobook/download"
                    st.markdown(f'<a href="{dl_url}" target="_blank"><button style="width:100%; height:40px; background-color:#10b981; color:white; border:none; border-radius:8px; font-weight:600; cursor:pointer;">📥 Download Audiobook File</button></a>', unsafe_allow_html=True)
                else:
                    st.button("Audiobook Not Compiled", disabled=True, use_container_width=True)

            # Media Player Component
            if audio_exists:
                st.markdown("### 🎵 Integrated Audiobook Player")
                active_audio_path = audiobook_mp3 if audiobook_mp3.exists() else audiobook_wav
                
                # Streamlit native audio player
                # We can serve the file or read it as bytes
                # Reading it as bytes handles downloads and playing locally beautifully
                with open(active_audio_path, "rb") as f:
                    audio_bytes = f.read()
                
                st.audio(audio_bytes, format="audio/mp3" if active_audio_path.suffix == ".mp3" else "audio/wav")
                st.info(f"Format: **{active_audio_path.suffix.upper()[1:]}** | File: `{active_audio_path.name}`")
                
            # Individual Chapter Players
            st.markdown("---")
            st.subheader("Individual Chapter Audio Tracks")
            for ch in chapters:
                if ch["audio_path"]:
                    ch_audio_path = Path(ch["audio_path"])
                    if ch_audio_path.exists():
                        with st.expander(f"🔊 Chapter {ch['order_index']}: {ch['title']} (Listening time: {int(ch['audio_duration'] // 60)}m {int(ch['audio_duration'] % 60)}s)", expanded=False):
                            with open(ch_audio_path, "rb") as f:
                                ch_audio_bytes = f.read()
                            st.audio(ch_audio_bytes, format="audio/wav")
                            ch_dl_url = f"{BACKEND_URL}/chapter/{ch['id']}/audio/download"
                            st.markdown(f'<a href="{ch_dl_url}" target="_blank">📥 Download Track</a>', unsafe_allow_html=True)

    with tab_settings:
        st.subheader("Workspace & AI Narrator Configurations")
        st.write("Adjust models and settings specifically for this project.")
        
        with st.form("settings_form"):
            model_opt = st.selectbox("AI Model Selection", list(SUPPORTED_MODELS.keys()), index=list(SUPPORTED_MODELS.keys()).index(project["model_name"]), format_func=lambda x: SUPPORTED_MODELS[x])
            temp = st.slider("Temperature", 0.1, 1.0, project["temperature"], 0.1)
            voice_opt = st.selectbox("Narrator Voice", SUPPORTED_VOICES, index=SUPPORTED_VOICES.index(project["tts_voice"]))
            speed = st.slider("Narrator Speed", 0.5, 2.0, project["tts_speed"], 0.1)
            audio_format = st.selectbox("Audio Output Format", ["wav", "mp3"], index=["wav", "mp3"].index(project["audio_format"]))
            
            if st.form_submit_button("Save Configurations"):
                payload = {
                    "model_name": model_opt,
                    "temperature": temp,
                    "tts_voice": voice_opt,
                    "tts_speed": speed,
                    "audio_format": audio_format
                }
                result = api_request("PUT", f"/project/{project_id}", json_data=payload)
                if result:
                    st.success("Configurations updated successfully!")
                    st.rerun()

# Auto-reload if there are active tasks to keep UI updated
if "has_active_jobs" in locals() and has_active_jobs:
    time.sleep(2)
    st.rerun()
