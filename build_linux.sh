#!/bin/bash
# SchoolLive Player – Linux bináris build

pip install -r requirements.txt

pyinstaller \
    --onefile \
    --name schoollive-player \
    main.py

echo ""
echo "Build kész: dist/schoollive-player"
