#!/usr/bin/env bash
# Compilazione su macOS/Linux. Dalla radice del progetto:  ./packaging/build.sh
set -euo pipefail
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python packaging/make_icons.py
.venv/bin/python -m PyInstaller packaging/csv_reader.spec --noconfirm --distpath dist --workpath build
case "$(uname -s)" in
  Darwin) echo "App: dist/CSV Revolution.app  (trascinala in Applicazioni; poi tasto destro su un .csv > Apri con)";;
  *)      echo "Eseguibile: dist/CSV Revolution/csv-revolution  ->  ./packaging/linux/install.sh per registrare «Apri con»";;
esac
