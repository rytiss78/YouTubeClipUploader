"""The app window: three setup steps, then Upload now."""
import json
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import uploader

CLOUD_URL = 'https://console.cloud.google.com/projectcreate'
API_URL = 'https://console.cloud.google.com/apis/library/youtube.googleapis.com'
PRIVACY = {'Public': 'public', 'Unlisted (anyone with the link)': 'unlisted', 'Private (only you)': 'private'}

HELP = """\
# Why is this needed?
YouTube only accepts uploads from apps you have approved, so you need your own free key from Google. You make it once; it takes about 5 minutes.

# 1. Create a project
Click "1. Create project" below and sign in with the Google account that owns your YouTube channel. Any project name works, for example "Clip Uploader". Click Create.

# 2. Turn on the YouTube API
Click "2. Enable YouTube API" below. Make sure your new project is selected at the top of the page, then click Enable.

# 3. Set up the sign-in screen
In the left menu (or the top search bar) open "Google Auth Platform", also called "OAuth consent screen", and click "Get started":
  • App name: anything, for example "Clip Uploader". Support email: yours.
  • Audience: External.
  • Contact email: yours. Agree to the policy and click Create.

# 4. Publish the app
Open "Audience" and click "Publish app", then confirm.
Important: if the app stays in "Testing", Google ends your sign-in every 7 days and automatic uploads stop until you sign in again.

# 5. Create the key
Open "Clients", then "Create client":
  • Application type: Desktop app
  • Name: anything, then Create.
Google then shows the new Client ID and Client secret.

# 6. Add it here
Either click "Paste key…" and paste the Client ID and Client secret, or download the JSON file, click "Choose file…" and pick it.
Lost the secret? Open the client in "Clients", click "Add secret", and paste the new one.

# Signing in
Click "Sign in…" and choose your channel. Google will warn that the app is not verified. That is normal for your own private key: click "Advanced", then "Go to Clip Uploader (unsafe)", and allow access.

# Good to know
  • Keep client_secret.json and token.json private. They give upload access to your channel.
  • A new Google project can upload about 6 videos per day. Extra clips wait for the next run.
  • Google may lock videos uploaded through a new, unaudited project as Private. If that happens, request the free YouTube API compliance audit from Google, linked from the YouTube Data API page.
"""


def resource(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / name


class QueueHandler(logging.Handler):
    """Shows log messages in the window (one line each; details stay in the log file)."""

    def __init__(self, events):
        super().__init__(logging.INFO)
        self.events = events

    def emit(self, record):
        if record.name != 'root' and record.levelno < logging.WARNING:
            return
        text = f'{time.strftime("%H:%M:%S", time.localtime(record.created))}  {record.getMessage()}'
        if record.exc_info and record.exc_info[1]:
            text += f' ({record.exc_info[1]})'
        self.events.put(('log', text))


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        try:
            self.cfg = uploader.load_config()
        except uploader.SetupError as exc:
            messagebox.showerror(uploader.APP_NAME, str(exc))
            self.cfg = json.loads(json.dumps(uploader.DEFAULT_CONFIG))
        logging.getLogger().addHandler(QueueHandler(self.events))

        root.title(uploader.APP_NAME)
        root.minsize(660, 640)
        try:
            root.iconbitmap(default=str(resource('icon.ico')))
        except tk.TclError:
            pass
        style = ttk.Style()
        style.configure('Good.TLabel', foreground='#1a7f37')
        style.configure('Bad.TLabel', foreground='#c62828')
        style.configure('Hint.TLabel', foreground='#666666')
        style.configure('Next.TLabel', font=('Segoe UI', 10, 'bold'))

        main = ttk.Frame(root, padding=14)
        main.pack(fill='both', expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=1)

        self.next_step = ttk.Label(main, style='Next.TLabel', wraplength=620)
        self.next_step.grid(row=0, column=0, sticky='w', pady=(0, 10))

        step1 = ttk.LabelFrame(main, text=' 1. Google API key ', padding=10)
        step1.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        step1.columnconfigure(0, weight=1)
        self.key_status = ttk.Label(step1)
        self.key_status.grid(row=0, column=0, sticky='w')
        ttk.Button(step1, text='How do I get this?', command=self.show_help).grid(row=0, column=1, padx=6)
        ttk.Button(step1, text='Paste key…', command=self.paste_key).grid(row=0, column=2)
        ttk.Button(step1, text='Choose file…', command=self.choose_key).grid(row=0, column=3, padx=(6, 0))

        step2 = ttk.LabelFrame(main, text=' 2. YouTube channel ', padding=10)
        step2.grid(row=2, column=0, sticky='ew', pady=(0, 8))
        step2.columnconfigure(0, weight=1)
        self.sign_status = ttk.Label(step2)
        self.sign_status.grid(row=0, column=0, sticky='w')
        self.sign_button = ttk.Button(step2, text='Sign in…', command=self.sign_in)
        self.sign_button.grid(row=0, column=1)

        step3 = ttk.LabelFrame(main, text=' 3. Settings ', padding=10)
        step3.grid(row=3, column=0, sticky='ew', pady=(0, 8))
        step3.columnconfigure(1, weight=1)
        self.watch = tk.StringVar(value=self.cfg.get('watch_folder') or uploader.guess_recordings_folder())
        self.uploaded = tk.StringVar(value=self.cfg.get('uploaded_folder', ''))
        labels = {v: k for k, v in PRIVACY.items()}
        self.privacy = tk.StringVar(value=labels.get(self.cfg.get('privacy'), 'Unlisted (anyone with the link)'))
        self.trim = tk.BooleanVar(value=bool(self.cfg.get('auto_prepare', {}).get('enabled')))
        self.autostart = tk.BooleanVar(value=uploader.autostart_enabled())

        ttk.Label(step3, text='Recordings folder').grid(row=0, column=0, sticky='w', pady=2)
        ttk.Entry(step3, textvariable=self.watch).grid(row=0, column=1, sticky='ew', padx=6)
        ttk.Button(step3, text='Browse…', command=lambda: self.browse(self.watch)).grid(row=0, column=2)
        ttk.Label(step3, text='Move uploaded clips to').grid(row=1, column=0, sticky='w', pady=2)
        ttk.Entry(step3, textvariable=self.uploaded).grid(row=1, column=1, sticky='ew', padx=6)
        ttk.Button(step3, text='Browse…', command=lambda: self.browse(self.uploaded)).grid(row=1, column=2)
        ttk.Label(step3, style='Hint.TLabel',
                  text='Leave empty to use <recordings folder>\\YouTubeUploader\\Uploaded. '
                       'One subfolder per game becomes one playlist per game.'
                  ).grid(row=2, column=1, columnspan=2, sticky='w', padx=6)
        ttk.Label(step3, text='Video visibility').grid(row=3, column=0, sticky='w', pady=(8, 2))
        ttk.Combobox(step3, textvariable=self.privacy, values=list(PRIVACY), state='readonly',
                     width=32).grid(row=3, column=1, sticky='w', padx=6, pady=(8, 2))
        ttk.Label(step3, text='Description').grid(row=4, column=0, sticky='nw', pady=(6, 2))
        self.description = tk.Text(step3, height=3, wrap='word', font=('Segoe UI', 9), relief='solid', borderwidth=1)
        self.description.insert('1.0', self.cfg.get('description', ''))
        self.description.grid(row=4, column=1, columnspan=2, sticky='ew', padx=6, pady=(6, 2))
        ttk.Label(step3, style='Hint.TLabel', text='{game} is replaced with the game name.'
                  ).grid(row=5, column=1, columnspan=2, sticky='w', padx=6)
        ttk.Checkbutton(step3, variable=self.trim,
                        text='Trim quiet, still footage from the start and end automatically'
                        ).grid(row=6, column=0, columnspan=3, sticky='w', pady=(8, 0))
        ttk.Checkbutton(step3, variable=self.autostart,
                        text='Upload new clips automatically each time I sign in to Windows'
                        ).grid(row=7, column=0, columnspan=3, sticky='w')
        ttk.Button(step3, text='Save settings', command=self.save_settings).grid(row=8, column=2, sticky='e')

        actions = ttk.Frame(main)
        actions.grid(row=4, column=0, sticky='ew', pady=(4, 4))
        self.upload_button = ttk.Button(actions, text='Upload now', command=self.upload_now)
        self.upload_button.pack(side='left')
        self.preview_button = ttk.Button(actions, text='Preview', command=self.preview)
        self.preview_button.pack(side='left', padx=6)
        ttk.Button(actions, text='Open app folder', command=lambda: os.startfile(uploader.BASE)).pack(side='right')
        ttk.Button(actions, text='Open full log', command=self.open_log).pack(side='right', padx=6)

        output = ttk.Frame(main)
        output.grid(row=5, column=0, sticky='nsew')
        output.columnconfigure(0, weight=1)
        output.rowconfigure(2, weight=1)
        self.status = ttk.Label(output, style='Hint.TLabel')
        self.status.grid(row=0, column=0, sticky='w')
        self.progress = ttk.Progressbar(output, maximum=100)
        self.progress.grid(row=1, column=0, sticky='ew', pady=(2, 6))
        self.log_view = ScrolledText(output, height=9, wrap='word', font=('Consolas', 9),
                                     state='disabled', relief='solid', borderwidth=1)
        self.log_view.grid(row=2, column=0, sticky='nsew')

        self.watch.trace_add('write', lambda *_: self.refresh_status())
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.refresh_status()
        if uploader.autostart_enabled():
            # Keep the sign-in shortcut pointing at this copy of the app.
            threading.Thread(target=self.repair_autostart, daemon=True).start()
        self.root.after(100, self.poll)

    # ------------------------------------------------------------ helpers

    def form_values(self):
        cfg = dict(self.cfg)
        cfg['watch_folder'] = self.watch.get().strip()
        cfg['uploaded_folder'] = self.uploaded.get().strip()
        cfg['privacy'] = PRIVACY[self.privacy.get()]
        cfg['description'] = self.description.get('1.0', 'end-1c').strip()
        cfg['auto_prepare'] = {**self.cfg.get('auto_prepare', {}), 'enabled': self.trim.get()}
        return cfg

    def refresh_status(self):
        has_key = uploader.CLIENT_SECRET_FILE.is_file()
        self.key_status.config(text='✔  Key added (client_secret.json)' if has_key else '✖  Not added yet',
                               style='Good.TLabel' if has_key else 'Bad.TLabel')
        signed_in = uploader.TOKEN_FILE.is_file()
        channel = self.cfg.get('channel_title')
        self.sign_status.config(
            text=(f'✔  Uploading to "{channel}"' if channel else '✔  Signed in') if signed_in else '✖  Not signed in',
            style='Good.TLabel' if signed_in else 'Bad.TLabel')
        self.sign_button.config(text='Switch channel…' if signed_in else 'Sign in…')
        problems = uploader.setup_problems(self.form_values())
        if problems:
            self.next_step.config(text='Next: ' + problems[0])
        elif uploader.autostart_enabled():
            self.next_step.config(text='All set. New clips upload when you sign in to Windows, or click "Upload now".')
        else:
            self.next_step.config(text='All set. Click "Upload now" to upload new clips.')

    def browse(self, variable):
        folder = filedialog.askdirectory(initialdir=variable.get() or str(Path.home() / 'Videos'))
        if folder:
            variable.set(str(Path(folder)))

    def append_log(self, line):
        self.log_view.config(state='normal')
        self.log_view.insert('end', line + '\n')
        self.log_view.see('end')
        self.log_view.config(state='disabled')

    def set_busy(self, text):
        self.busy = bool(text)
        for button in (self.upload_button, self.preview_button, self.sign_button):
            button.config(state='disabled' if self.busy else 'normal')
        self.status.config(text=text or '')
        if not self.busy:
            self.progress['value'] = 0

    def start_task(self, work, on_success, busy_text):
        """Run slow work off the window thread; on_success gets its result back on it."""
        self.set_busy(busy_text)

        def runner():
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
                if isinstance(exc, (uploader.SetupError, uploader.AlreadyRunning)):
                    logging.warning('%s', exc)
                else:
                    logging.exception('Stopped')
            self.events.put(('done', on_success, result, error))
        threading.Thread(target=runner, daemon=True).start()

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == 'log':
                    self.append_log(event[1])
                elif event[0] == 'progress':
                    self.progress['value'] = event[1] * 100
                elif event[0] == 'done':
                    _, on_success, result, error = event
                    self.set_busy(None)
                    try:
                        if error:
                            messagebox.showerror(uploader.APP_NAME, str(error))
                        else:
                            on_success(result)
                    except Exception as exc:  # keep the window responsive whatever happens
                        logging.exception('Stopped')
                        messagebox.showerror(uploader.APP_NAME, str(exc))
                    self.refresh_status()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def repair_autostart(self):
        try:
            uploader.set_autostart(True)
        except Exception:
            logging.warning('Could not update the Windows sign-in shortcut.', exc_info=True)

    # ------------------------------------------------------------ actions

    def show_help(self):
        window = tk.Toplevel(self.root)
        window.title('Getting your Google API key')
        window.transient(self.root)
        text = ScrolledText(window, wrap='word', width=82, height=32, font=('Segoe UI', 10),
                            padx=12, pady=10, relief='flat')
        text.tag_configure('heading', font=('Segoe UI', 10, 'bold'), spacing1=4)
        for line in HELP.splitlines():
            if line.startswith('# '):
                text.insert('end', line[2:] + '\n', 'heading')
            else:
                text.insert('end', line + '\n')
        text.config(state='disabled')
        text.pack(fill='both', expand=True)
        buttons = ttk.Frame(window, padding=8)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='1. Create project', command=lambda: webbrowser.open(CLOUD_URL)).pack(side='left')
        ttk.Button(buttons, text='2. Enable YouTube API', command=lambda: webbrowser.open(API_URL)).pack(side='left', padx=6)
        ttk.Button(buttons, text='Paste key…', command=lambda: (window.destroy(), self.paste_key())).pack(side='left')
        ttk.Button(buttons, text='Choose file…', command=lambda: (window.destroy(), self.choose_key())).pack(side='left', padx=6)
        ttk.Button(buttons, text='Close', command=window.destroy).pack(side='right')

    def choose_key(self):
        path = filedialog.askopenfilename(
            title='Choose the client_secret JSON file you downloaded from Google Cloud',
            initialdir=str(Path.home() / 'Downloads'),
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')])
        if not path:
            return
        try:
            uploader.install_client_secret(path)
        except uploader.SetupError as exc:
            messagebox.showerror('Not the right file', str(exc))
            return
        self.key_added()

    def paste_key(self):
        window = tk.Toplevel(self.root)
        window.title('Paste your Google key')
        window.transient(self.root)
        window.resizable(False, False)
        form = ttk.Frame(window, padding=14)
        form.pack(fill='both', expand=True)
        ttk.Label(form, wraplength=480,
                  text='In Google Cloud, open "Clients" and click your Desktop client. Copy its '
                       'Client ID and Client secret, and paste them here with Ctrl+V.'
                  ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 10))
        client_id, client_secret = tk.StringVar(), tk.StringVar()
        ttk.Label(form, text='Client ID').grid(row=1, column=0, sticky='w', pady=3)
        id_entry = ttk.Entry(form, textvariable=client_id, width=62)
        id_entry.grid(row=1, column=1, padx=(8, 0))
        ttk.Label(form, text='Client secret').grid(row=2, column=0, sticky='w', pady=3)
        ttk.Entry(form, textvariable=client_secret, width=62, show='•').grid(row=2, column=1, padx=(8, 0))

        def save(*_):
            try:
                uploader.install_client_keys(client_id.get(), client_secret.get())
            except uploader.SetupError as exc:
                messagebox.showerror('Not quite right', str(exc), parent=window)
                return
            window.destroy()
            self.key_added()

        buttons = ttk.Frame(form)
        buttons.grid(row=3, column=0, columnspan=2, sticky='e', pady=(12, 0))
        ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='right')
        ttk.Button(buttons, text='Save key', command=save).pack(side='right', padx=6)
        window.bind('<Return>', save)
        window.bind('<Escape>', lambda _: window.destroy())
        id_entry.focus_set()
        window.grab_set()

    def key_added(self):
        logging.info('Google API key added.')
        self.refresh_status()
        if not uploader.TOKEN_FILE.is_file() and messagebox.askyesno(uploader.APP_NAME, 'Key added. Sign in to YouTube now?'):
            self.sign_in()

    def sign_in(self):
        if not uploader.CLIENT_SECRET_FILE.is_file():
            messagebox.showinfo(uploader.APP_NAME, 'Add your Google API key first (step 1).')
            return
        logging.info('Opening your browser for Google sign-in…')
        self.start_task(uploader.sign_in, self.signed_in, 'Waiting for you to finish signing in in your browser…')

    def signed_in(self, result):
        creds, channel = result
        expected = self.cfg.get('expected_channel_id')
        if expected and expected != channel['id'] and not messagebox.askyesno(
                'Different channel', f'You signed in to "{channel["title"]}", which is not the channel '
                                     f'used before.\n\nUpload to "{channel["title"]}" from now on?'):
            logging.info('Kept the previous channel.')
            return
        uploader.save_sign_in(creds)
        saved = uploader.update_config(expected_channel_id=channel['id'], channel_title=channel['title'])
        self.cfg.update(expected_channel_id=saved['expected_channel_id'], channel_title=saved['channel_title'])
        logging.info('Signed in. Uploads go to "%s".', channel['title'])

    def save_settings(self, quiet=False):
        cfg = self.form_values()
        if cfg['watch_folder'] and not Path(cfg['watch_folder']).is_dir():
            messagebox.showerror(uploader.APP_NAME, f'Recordings folder not found:\n{cfg["watch_folder"]}')
            return False
        if cfg['watch_folder'] and cfg['uploaded_folder'] and \
                Path(cfg['uploaded_folder']).resolve() == Path(cfg['watch_folder']).resolve():
            messagebox.showerror(uploader.APP_NAME, 'Uploaded clips need their own folder, '
                                                    'not the recordings folder itself.')
            return False
        try:
            uploader.save_config(cfg)
            self.cfg = cfg
            if self.autostart.get() != uploader.autostart_enabled():
                uploader.set_autostart(self.autostart.get())
        except Exception as exc:
            messagebox.showerror('Could not save settings', str(exc))
            return False
        if not quiet:
            logging.info('Settings saved.')
        self.refresh_status()
        return True

    def upload_now(self):
        if self.busy or not self.save_settings(quiet=True):
            return
        problems = uploader.setup_problems(self.cfg)
        if problems:
            messagebox.showwarning('Finish setup first', '\n'.join(problems))
            return
        self.start_task(lambda: uploader.run_once(on_progress=lambda f: self.events.put(('progress', f))),
                        self.upload_finished, 'Uploading… You can keep using your PC.')

    def upload_finished(self, summary):
        if not summary['found']:
            logging.info('No new clips in the recordings folder.')
            return
        parts = [f"{len(summary['uploaded'])} uploaded"]
        if summary['failed']:
            parts.append(f"{len(summary['failed'])} failed (they stay in place and are retried next time)")
        if summary['waiting']:
            parts.append(f"{len(summary['waiting'])} still being recorded or copied")
        self.status.config(text='Finished: ' + ', '.join(parts) + '.')
        if summary['stopped']:
            messagebox.showinfo(uploader.APP_NAME, summary['stopped'])

    def preview(self):
        if self.busy or not self.save_settings(quiet=True):
            return
        self.start_task(uploader.preview, self.show_preview, 'Looking for clips…')

    def show_preview(self, clips):
        if not clips:
            logging.info('No new clips in the recordings folder.')
            return
        logging.info('%d clip(s) would be uploaded:', len(clips))
        for path, metadata in clips:
            logging.info('   %s  →  %s', path.name, metadata if isinstance(metadata, str) else metadata['title'])

    def open_log(self):
        if uploader.LOG_FILE.exists():
            os.startfile(uploader.LOG_FILE)

    def on_close(self):
        if self.busy and not messagebox.askyesno(
                uploader.APP_NAME, 'Something is still running. Close anyway?\n\n'
                                   'An unfinished upload starts again next time.'):
            return
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on high-DPI screens
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
