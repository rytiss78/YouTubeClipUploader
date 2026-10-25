# Replaces the stock PyInstaller hook, which bundles all ~550 Google API
# descriptions (94 MB, unpacked on every start). Only YouTube's is needed.
from pathlib import Path

import googleapiclient
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata('google_api_python_client')
datas.append((str(Path(googleapiclient.__file__).parent / 'discovery_cache' / 'documents' / 'youtube.v3.json'),
              'googleapiclient/discovery_cache/documents'))
