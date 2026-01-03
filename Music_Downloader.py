import yt_dlp
import os
import shutil
import re
import requests
import time
import sys
import spotipy
import concurrent.futures
import json
import difflib
from threading import Lock
from collections import deque
from pathlib import Path

from spotipy.oauth2 import SpotifyClientCredentials
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC

# ---------------- RICH UI ----------------
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    TaskID
)
from rich.panel import Panel
from rich.layout import Layout
from rich import box

# ---------------- SELENIUM ----------------
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

# ================= CONFIGURATION =================
BASE_DIR = Path("Glanza_Music_USB")
TEMP_DIR = BASE_DIR / "_temp_downloads"
HISTORY_FILE = BASE_DIR / "song_history.json"
MAX_WORKERS = 4

# --- API KEYS ---
SPOTIPY_CLIENT_ID = "YOUR_CLIENT_ID"
SPOTIPY_CLIENT_SECRET = "YOUR_CLIENT_SECRET"

FOLDERS = {
    "1": ("Kannada - New", "Kannada New"),
    "2": ("Kannada - Old", "Kannada Old"),
    "3": ("Kannada - Mix", "Kannada Mix"),
    "4": ("Hindi", "Hindi Bollywood"),
    "5": ("English", "English Pop"),
    "6": ("Others", "South Indian Mix"),
}

# Global State
console = Console()
logs = deque(maxlen=8)
ui_lock = Lock()
stats = {"downloaded": 0, "skipped": 0, "error": 0}

# ================= CORE CLASSES =================

class HistoryManager:
    """Handles the JSON Brain with O(1) lookups."""
    def __init__(self, filepath):
        self.filepath = filepath
        self.lock = Lock()
        self.data = self._load()

    def _load(self):
        if not self.filepath.exists():
            return {}
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            # Migrate list to dict if needed
            if isinstance(raw_data, list):
                return {entry.get('clean_title', '').lower(): entry for entry in raw_data if 'clean_title' in entry}
            return raw_data
        except Exception:
            return {}

    def save(self):
        with self.lock:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)

    def add(self, song_meta):
        key = song_meta['clean_title'].lower()
        with self.lock:
            self.data[key] = song_meta
        self.save()

    def exists(self, title):
        return title.lower() in self.data

class DuplicateGuard:
    def __init__(self, history_manager):
        self.history = history_manager

    def is_duplicate(self, title):
        clean_input = title.lower().strip()
        if self.history.exists(clean_input):
            return True, "History Match"
        
        # Fuzzy match (similarity > 85%)
        existing_titles = self.history.data.keys()
        matches = difflib.get_close_matches(clean_input, existing_titles, n=1, cutoff=0.85)
        if matches:
            return True, f"Similar: '{matches[0]}'"
        return False, None

class FileManager:
    def __init__(self):
        if not BASE_DIR.exists(): BASE_DIR.mkdir()
        if not TEMP_DIR.exists(): TEMP_DIR.mkdir()
        # Clean old temp files on startup
        for f in TEMP_DIR.glob("*.mp3"):
            try: f.unlink()
            except: pass

    def get_temp_path(self, unique_id):
        # Use a SAFE unique filename in temp (e.g., task_12.mp3)
        return TEMP_DIR / f"task_{unique_id}.mp3"

    def get_final_path(self, folder_name, filename):
        return BASE_DIR / folder_name / filename

    def safe_move(self, temp_path, final_path):
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            try: final_path.unlink() # Overwrite existing
            except: pass
        shutil.move(str(temp_path), str(final_path))

    def clean_duplicates_in_folder(self, folder_name, new_file_name):
        target_dir = BASE_DIR / folder_name
        if not target_dir.exists(): return

        clean_target = StringUtils.clean_title(new_file_name.replace(".mp3", "")).lower()
        
        for file in target_dir.iterdir():
            if file.name == new_file_name: continue
            if not file.suffix == ".mp3": continue
            
            existing_clean = StringUtils.clean_title(file.stem).lower()
            if existing_clean == clean_target:
                try:
                    file.unlink()
                    Logger.log(f"Del Old: {file.name}", "red")
                except: pass

class StringUtils:
    @staticmethod
    def clean_title(title):
        # 1. Aggressive Split: Stop at first Pipe | or Dash -
        if "|" in title: title = title.split("|")[0]
        if " - " in title: title = title.split(" - ")[0]
        
        # 2. Remove Junk
        junk = [
            r"\(.*?\)", r"\[.*?\]", "Official", "Video", "Audio", "HD", "4K", 
            "Song", "Lyrical", "Lyrics", "Kannada", "Telugu", "Tamil", "Hindi", 
            "Malayalam", "Super Hit", "Old", "New", "Full", "Movie", "Jukebox"
        ]
        for j in junk:
            title = re.sub(j, "", title, flags=re.IGNORECASE)
            
        # 3. Clean special chars that confuse filesystem
        title = re.sub(r'[\\/*?:"<>|]', "", title) 
            
        return " ".join(title.split()).strip()

class Logger:
    @staticmethod
    def log(msg, style="white"):
        with ui_lock:
            ts = time.strftime('%H:%M:%S')
            logs.append(f"[{style}]{ts} {msg}[/]")

# Initialize Singletons
history_db = HistoryManager(HISTORY_FILE)
dup_guard = DuplicateGuard(history_db)
file_mgr = FileManager()

# ================= EXTRACTORS =================
def get_playlist_tracks(url):
    if "music.apple.com" in url:
        return _fetch_apple_selenium(url)
    elif "spotify.com" in url:
        return _fetch_spotify(url)
    else:
        return [url]

def _fetch_apple_selenium(url):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--log-level=3")
    Logger.log("Launching Browser...", "yellow")
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(url)
        time.sleep(5)
        songs = []
        # Strategy 1: Aria Labels (Table Rows)
        for row in driver.find_elements(By.CSS_SELECTOR, "div[role='row']"):
            lbl = row.get_attribute("aria-label")
            if lbl and "Song" not in lbl: # Filter out headers if possible
                songs.append(lbl)
            elif lbl:
                 songs.append(lbl)

        # Strategy 2: Track Lockups (Grid View)
        if not songs:
            for el in driver.find_elements(By.CSS_SELECTOR, "div[data-testid='track-lockup']"):
                text = el.text.split("\n")[0] # First line usually title
                if text: songs.append(text + " Audio")
        
        driver.quit()
        unique_songs = list(set(songs))
        Logger.log(f"Extracted {len(unique_songs)} songs", "green")
        return unique_songs
    except Exception as e:
        Logger.log(f"Browser Error: {e}", "red")
        return []

def _fetch_spotify(url):
    try:
        Logger.log("Authenticating Spotify...", "yellow")
        auth = SpotifyClientCredentials(client_id=SPOTIPY_CLIENT_ID, client_secret=SPOTIPY_CLIENT_SECRET)
        sp = spotipy.Spotify(auth_manager=auth)
        results = sp.playlist_tracks(url)
        tracks = results['items']
        while results['next']:
            results = sp.next(results)
            tracks.extend(results['items'])
        return [f"{t['track']['name']} {t['track']['artists'][0]['name']}" for t in tracks if t['track']]
    except Exception: return []

# ================= PROCESSING ENGINE =================
def process_song_task(song_obj, folder_name, genre, progress):
    task_id = song_obj['task']
    # Ensure task_id is a string or int for filename usage
    if isinstance(task_id, TaskID):
        task_id_val = int(task_id)
    else:
        task_id_val = str(task_id)

    raw_query = song_obj['name']
    
    # 1. CLEAN & CHECK
    pre_clean = StringUtils.clean_title(raw_query)
    is_dup, reason = dup_guard.is_duplicate(pre_clean)
    if is_dup:
        # Don't hide, show SKIP state
        song_obj['state'] = f'[yellow]⚠ Skip ({reason})[/]'
        with ui_lock: stats["skipped"] += 1
        # progress.update(task_id, visible=False) # Keep visible for report
        time.sleep(0.5) # Let user see it
        return

    # 2. DOWNLOAD (Deterministic Filename)
    song_obj['state'] = '⬇ Downloading'
    
    temp_path = file_mgr.get_temp_path(task_id_val)
    
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            done = d.get('downloaded_bytes', 0)
            if total: progress.update(task_id, total=total, completed=done)

    dl_link = raw_query if "http" in raw_query else f"ytsearch1:{raw_query}"

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(temp_path).replace(".mp3", ""), # yt-dlp adds extension
        'progress_hooks': [hook],
        'quiet': True,
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}],
    }

    try:
        info = None
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(dl_link, download=True)
            if 'entries' in info: info = info['entries'][0]

        # Verify File Exists
        if not temp_path.exists():
            raise FileNotFoundError(f"Temp file {temp_path} not found")

        # 3. TAGGING (Fail-safe)
        song_obj['state'] = '🏷 Tagging'
        
        final_title = StringUtils.clean_title(info['title'])
        if not final_title: final_title = pre_clean 
        
        db_title, db_artist, db_album = _apply_metadata(temp_path, final_title, genre)
        
        if db_title: final_title = StringUtils.clean_title(db_title)

        final_filename = f"{final_title}.mp3"
        final_path = file_mgr.get_final_path(folder_name, final_filename)

        # 4. MOVE
        file_mgr.clean_duplicates_in_folder(folder_name, final_filename)
        file_mgr.safe_move(temp_path, final_path)

        # 5. MEMORY
        history_db.add({
            "clean_title": final_title,
            "original": raw_query,
            "path": str(final_path),
            "date": time.strftime("%Y-%m-%d")
        })

        song_obj['state'] = '[green]✔ Done[/]'
        with ui_lock: stats["downloaded"] += 1
        Logger.log(f"Added: {final_title}", "green")

    except Exception as e:
        song_obj['state'] = '[red]✖ Error[/]'
        with ui_lock: stats["error"] += 1
        Logger.log(f"Err {raw_query[:10]}: {str(e)[:20]}", "red")
        if temp_path.exists(): 
            try: temp_path.unlink()
            except: pass

def _apply_metadata(path, title, genre):
    """Returns (Title, Artist, Album) if found, else None."""
    try:
        url = "https://itunes.apple.com/search"
        search_term = title.split("-")[0].strip()
        
        data = requests.get(url, params={"term": search_term, "country": "IN", "media": "music", "limit": 1}, timeout=3).json()
        
        audio = MP3(path, ID3=EasyID3)
        
        if data['resultCount']:
            track = data['results'][0]
            t, a, al = track['trackName'], track['artistName'], track['collectionName']
            
            audio['title'] = t
            audio['artist'] = a
            audio['album'] = al
            audio['genre'] = genre
            audio.save()
            
            try:
                img_url = track['artworkUrl100'].replace("100x100", "600x600")
                img = requests.get(img_url).content
                audio = MP3(path, ID3=ID3)
                audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img))
                audio.save()
            except: pass
            
            return t, a, al
        else:
            audio['title'] = title
            audio['genre'] = genre
            audio.save()
            return title, "Unknown", "Unknown"
    except:
        return None, None, None

# ================= UI & MAIN LOOP =================
def build_ui(songs, progress):
    active_i = 0
    for i, s in enumerate(songs):
        if "Done" not in s['state'] and "Skip" not in s['state'] and "Error" not in s['state']:
            active_i = i
            break
            
    start = max(0, active_i - 2)
    end = min(len(songs), start + 8)

    layout = Layout()
    layout.split(
        Layout(name="main", ratio=1),
        Layout(name="footer", size=10)
    )
    layout["main"].split_row(
        Layout(name="queue", ratio=2),
        Layout(name="logs", ratio=1)
    )

    table = Table(box=box.SIMPLE, expand=True)
    table.add_column("#", width=3, style="dim")
    table.add_column("Song", ratio=1)
    table.add_column("State", width=15)

    for i in range(start, end):
        s = songs[i]
        name = s['name'][:35] + "..." if len(s['name']) > 35 else s['name']
        style = "bold white" if i == active_i else "dim"
        table.add_row(str(i+1), name, s['state'], style=style)
        
    if end < len(songs): table.add_row("...", f"+{len(songs)-end}", "")

    layout["main"]["queue"].update(Panel(table, title="Download Queue", border_style="cyan"))
    layout["main"]["logs"].update(Panel("\n".join(logs), title="System Logs", border_style="green"))
    layout["footer"].update(Panel(progress, title="Active Tasks", border_style="magenta"))

    return layout

if __name__ == '__main__':
    while True:
        console.clear()
        console.rule("[bold red]GLANZA MUSIC ARCHITECT v3.2[/]")
        
        # Reset Stats
        stats = {"downloaded": 0, "skipped": 0, "error": 0}

        menu = Table(show_header=False, box=box.ROUNDED)
        for k, v in FOLDERS.items(): menu.add_row(f"[{k}]", v[0])
        console.print(menu)

        choice = console.input("\n[bold yellow]Select Category (q to quit): [/]").strip()
        if choice.lower() == 'q': sys.exit()
        
        folder_data = FOLDERS.get(choice)
        if not folder_data: continue
        folder_name, genre = folder_data

        raw_input = console.input(f"[bold]Paste Link or Search for '{folder_name}': [/]")
        
        with console.status("Fetching track list..."):
            track_list = get_playlist_tracks(raw_input)
            
        if not track_list:
            console.print("[red]No tracks found![/]")
            time.sleep(2)
            continue
        
        console.print(f"[bold green]Found {len(track_list)} songs. Starting...[/]")
        time.sleep(1)

        songs = [{'name': t, 'state': 'Pending'} for t in track_list]
        
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn()
        )
        
        for s in songs:
            s['task'] = progress.add_task(s['name'], start=False, visible=False)

        with Live(console=console, refresh_per_second=10, screen=True) as live:
            with concurrent.futures.ThreadPoolExecutor(MAX_WORKERS) as exe:
                futures = []
                for s in songs:
                    progress.update(s['task'], visible=True)
                    progress.start_task(s['task'])
                    futures.append(exe.submit(process_song_task, s, folder_name, genre, progress))
                
                while any(not f.done() for f in futures):
                    live.update(build_ui(songs, progress))
                    time.sleep(0.1)
                live.update(build_ui(songs, progress))

        console.print(f"[bold green]Batch Complete![/] (Dow: {stats['downloaded']} | Skip: {stats['skipped']} | Err: {stats['error']})")
        console.print("[dim]Press Enter to continue...[/]")
        input()