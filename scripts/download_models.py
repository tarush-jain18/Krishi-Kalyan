import os
from pathlib import Path

import gdown

MODEL_DIR = Path("app/ml/models")
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FILES = {
    "fertilizer_model.pkl": "1kLc6rxM-YFeoB3C4xJw2ccK04UX_iWnb",
    "crop_model.pkl": "1dyvk2K3QfwsVbiRKLIrmww4onhT85qAQ",
    "final_pest_model.keras": "1Xxx3grvPmWxNPgZLD_OCJxqeZCO6Sj6L",
    "irrigation_model.pkl": "1qHJisVX8hSpaxuf47GXJ-xf3VZpDip-o",
    "label_encoder.pkl": "1d_nDOYVT_AvJXhOHH3w70-yP5mJJL2su",
    "pest_model.keras": "1tWxCRcppSe8hNt2xs-UiY27Eb2TOPr76",
    "scaler.pkl": "1fgiZ4QWYnHfoP9dVnRlPcNsZxK2uqCf8",
}

for filename, file_id in FILES.items():

    destination = MODEL_DIR / filename

    if destination.exists():
        print(f"✓ {filename} already exists")
        continue

    print(f"Downloading {filename}...")

    url = f"https://drive.google.com/uc?id={file_id}"

    gdown.download(
        url,
        str(destination),
        quiet=False,
    )

print("\nAll models downloaded successfully.")