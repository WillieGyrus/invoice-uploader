#!/bin/bash
# Runs invoice_uploader.py only on the last day of the month.
# Called daily by launchd; exits silently on any other day.

tomorrow_day=$(date -v+1d +%-d)
if [ "$tomorrow_day" -ne 1 ]; then
    exit 0
fi

current_month=$(date +%-m)
cd /Users/williemac/Projects/Gyrus/Internal/invoice-uploader

exec /usr/bin/python3 invoice_uploader.py "$current_month"
