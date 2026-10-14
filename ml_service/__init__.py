"""Local prediction service that exposes the dual-stream LSTM to the trading platform."""
import os

# Headless: legacy plotting helpers call plt.show(), which must never open a window
# or block inside the service.
os.environ.setdefault("MPLBACKEND", "Agg")
