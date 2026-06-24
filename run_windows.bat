@echo off
echo ============================================
echo   PDF Bookmark ^& Navigation Tool
echo ============================================
echo.
echo Installing/checking dependencies...
pip install flask pypdf pdfplumber reportlab --quiet
echo.
echo Starting server...
echo Open your browser at: http://localhost:5050
echo.
echo To share with your team on the same network:
echo   Run ipconfig and use your IPv4 address
echo   e.g. http://192.168.1.100:5050
echo.
python app.py
pause
