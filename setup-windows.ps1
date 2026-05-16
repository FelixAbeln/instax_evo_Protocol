py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Write-Host ""
Write-Host "Setup done. Try:"
Write-Host "python -m instax_lab scan --timeout 10"
