#!/usr/bin/env bash
# Registra CSV Revolution per l'utente corrente: voce di menu e «Apri con» per i file .csv.
# Dalla radice del progetto, dopo la compilazione:  ./packaging/linux/install.sh
set -euo pipefail
SRC="dist/CSV Revolution"
[ -x "$SRC/csv-revolution" ] || { echo "Compila prima con ./packaging/build.sh"; exit 1; }
DEST="$HOME/.local/opt/csv-revolution"
mkdir -p "$DEST" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
rm -rf "$DEST" && mkdir -p "$DEST" && cp -r "$SRC"/. "$DEST"/
ln -sf "$DEST/csv-revolution" "$HOME/.local/bin/csv-revolution"
cp packaging/icons/csv_reader.png "$HOME/.local/share/icons/hicolor/256x256/apps/csv-revolution.png"
sed "s|^Exec=.*|Exec=$DEST/csv-revolution %F|" packaging/linux/csv-revolution.desktop > "$HOME/.local/share/applications/csv-revolution.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "Fatto. Tasto destro su un .csv > Apri con > CSV Revolution."
echo "Per renderla l'app predefinita:  xdg-mime default csv-revolution.desktop text/csv"
