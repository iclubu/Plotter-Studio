#!/bin/bash
git add .
echo "Enter your commit message:"
read message
if [ -z "$message" ]; then
    git commit -m "Automated update: $(date +'%Y-%m-%d %H:%M')"
else
    git commit -m "$message"
fi
git push origin main
echo "------------------------------"
echo "✅ Project pushed to GitHub!"
