"""Build YouTubeClipUploader.exe. Run build.bat, which installs the tools first."""
import shutil
from pathlib import Path

import PyInstaller.__main__

HERE = Path(__file__).resolve().parent
NAME = 'YouTubeClipUploader'

PyInstaller.__main__.run([
    str(HERE / 'app.py'), '--name', NAME, '--onefile', '--windowed', '--noconfirm', '--clean',
    '--icon', str(HERE / 'icon.ico'),
    '--add-data', f'{HERE / "icon.ico"};.',
    '--additional-hooks-dir', str(HERE / 'hooks'),  # bundle only the YouTube API description
    '--collect-data', 'imageio_ffmpeg',  # ffmpeg for automatic trimming
    '--distpath', str(HERE / 'dist'), '--workpath', str(HERE / 'build'), '--specpath', str(HERE / 'build'),
])

target = HERE.parent / f'{NAME}.exe'
try:
    shutil.copy2(HERE / 'dist' / f'{NAME}.exe', target)
    print(f'\nBuilt {target}')
except PermissionError:
    print(f'\nBuilt {HERE / "dist" / NAME}.exe, but could not replace {target}. Close the app and copy it manually.')
