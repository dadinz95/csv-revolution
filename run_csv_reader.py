"""Punto d'ingresso per l'eseguibile (PyInstaller) e per l'avvio con doppio clic."""
import sys

from csv_reader.desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
