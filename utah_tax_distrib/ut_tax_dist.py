#!/usr/bin/python

'''
!!! WARNING !!!

This script will overwrite PDF and text files in your current working directory

!!! WARNING !!!

Incorrect use of this script could overwhelm a government server -- use at your
own risk!

Assumes the binary 'pdftotext' is in your $PATH

Usage - parse existing PDFs:            ut_tax_dist.py
Usage - download and then parse PDFs:   ut_tax_dist.py download
Usage - parse existing textfiles:       ut_tax_dist.py textonly

Send bugs to Mathew White <mathew.b.white@gmail.com>

Note that the CSV output includes all columns common to the entire dataset.
If a column doesn't exist in a file, it is left blank.

Source data: https://tax.utah.gov/sales/distribution

TODO: shorttermleasing has two reports in one document

'''

import re
import csv
import md5
import sys
import glob
import time
import shutil
import os.path
import requests
from bs4 import BeautifulSoup
from subprocess import check_output
from collections import defaultdict

# Common Columns
comcols = ('YEAR MONTH TAXTYPE JCODE').split(' ')


# Trim unwanted characters (leading/trailing spaces, commas, dollar-signs)
def trimit(v):
    return re.sub(r'(^\s+|\s+$|[,\$])', '', v)


# Convert a formatted line to an array
def line2cols(y, m, tax, line, hcols):
    ary = list(re.match(
        (
            r'^\s+(\d{5})'                              # Jurisdiction Code
            + r'\s+([^\$\d]+)'                          # City
            + r'([\$\d\,\.-]+)\s+' * (len(hcols) - 2)   # Middle Cols
            + r'([\$\d\,\.-]+)'                         # Final Col
        ),
        line
    ).groups())
    #).group(*range(1, len(hcols) + 3)))
    return dict(zip(
        comcols + hcols,
        [y, m, tax] + list(map(trimit, ary))
    ))


# Parse header line
def headparse(line):
    cols = re.split(r'\s\s+', line)
    if cols[1] == 'CITY':
        cols = cols[2:]
    elif cols[1] == 'PSAP':
        cols = cols[1:]
        cols[0] = 'LOCALITY'
    elif cols[1] == 'TOTAL DISTRIB':
        cols = ['LOCALITY'] + cols[1:]
    else:
        cols = cols[1:]

    ret = []
    for c in cols:
        if c == 'TOTAL DISTRIB TOTAL DEDUCT':
            ret += ['TOTAL DISTRIB', 'TOTAL DEDUCT']
        elif c == 'CHARITABLE OTHER DEDUCT':
            ret += ['CHARITABLE', 'OTHER DEDUCT']
        elif c == 'FINAL DISTRIB BALANCE OWED':
            ret += ['FINAL DISTRIB', 'BALANCE OWED']
        elif c == 'INTER AGRMT FINAL DISTRIB BALANCE OWED':
            ret += ['INTER AGRMT', 'FINAL DISTRIB', 'BALANCE OWED']
        else:
            ret += [c]

    ret = [re.sub(r'\s', '_', x) for x in ret]

    return ret


# Parse all the given text files
def parseit(txts):
    stor = []
    col_place = defaultdict(int)    # Store the default placement of a header
    col_count = defaultdict(int)    # Store the number of times a header is
    colstats = {}                   # Put the columns in their "usual" order
    for t in txts:
        print("Processing %s" % t)
        uniq = {}
        (y, m, tax) = re.match(r'\./(\d\d)(\d\d)(.*)\.txt', t).group(1, 2, 3)
        y = '20' + y
        print("Year: %s Month: %s Tax: %s" % (y, m, tax))
        headsentinel = 0
        hcols = []
        with open(t, 'r') as file:
            for line in file:
                line = line.rstrip()

                # The 'shorttermleasing' files contain both taxes and revenues
                # Ignore revenues and only record taxes
                if tax == 'shorttermleasing' and re.search(r'REVENUES', line):
                    break

                # Verify the uniqueness of the line (after normalizing spaces)
                # (sometimes pdftotext will duplicate a line at the top
                #  or bottom of a page)
                vetted = line
                while re.search(r'\s\s', vetted):
                    vetted = re.sub(r'\s\s', ' ', vetted)
                hh = md5.new(vetted).hexdigest()
                if hh in uniq:
                    continue
                else:
                    uniq[hh] = 1

                # Move on if we have already successfully parsed a header
                if len(hcols) == 0:
                    # If the line is a percursor header record,
                    # set the flag to look for the header next
                    if re.search(r'CNTY', line):
                        headsentinel = 1

                    # If the line is a header record, parse it as header
                    if headsentinel == 1 and re.search(r'TOTAL', line):
                        # Remove 'CNTY' since it isn't consistent
                        line = re.sub(r'(CNTY /|CNTY/|CNTY)', '', line)
                        hcols = headparse(line)
                        for i, c in enumerate(hcols):
                            col_count[c] += 1
                            col_place[c] += i + 1

                # If the line is a data record, parse it as data
                if re.match(r'^\s+\d{5}', line):
                    stor.append(line2cols(y, m, tax, line, hcols))

    # Calculate the "order" of the headers
    for k in col_place.keys():
        colstats[k] = float(col_place[k]) / float(col_count[k])

    return (stor, colstats)


# Save all the parsed data
def storeit(stor, colstats):
    sk = sorted(colstats, key=lambda x: colstats[x])
    with open('ut_tax.csv', 'wb') as csvfile:
        wr = csv.writer(csvfile, quoting=csv.QUOTE_NONNUMERIC)
        wr.writerow(comcols + sk)
        for s in stor:
            wrline = [s[x] for x in comcols]
            for k in sk:
                if k in s:
                    wrline += [s[k]]
                else:
                    wrline += ['']

            wr.writerow(wrline)


# Get all the PDF files from the salestax PDF links on the give URL
def getfiles(url):
    # Get the main URL
    r = requests.get(url, verify=False)
    soup = BeautifulSoup(r.text, 'html.parser')

    # For each PDF link, download it if it doesn't already exist
    for link in soup.find_all('a'):
        ref = link.get('href')
        if ref is not None and re.match(r'/salestax/distribute/\d\d\d\d', ref):
            # Get the filename from the path
            # For security, ensure filename has only desired characters
            fn = re.match(
                r'/salestax/distribute/(\d\d\d\d[\w\-\.]+)',
                ref
            ).group(1)

            # Check to see if we already downloaded it
            if os.path.isfile(fn):
                print("%s is already downloaded" % fn)
                continue

            # Get the file and save it
            print('Get %s FN %s' % (ref, fn))
            r = requests.get(
                ('https://tax.utah.gov%s' % ref),
                verify=False, stream=True
            )

            if r.status_code == 200:
                with open(fn, 'wb') as f:
                    r.raw.decode_content = True
                    shutil.copyfileobj(r.raw, f)
            else:
                print("Skipping %s due to error!" % fn)

            # Put in a timer to prevent a denial of service block
            time.sleep(3)


# Just parse the PDF files
def do_pdfs():
    # Get PDF files
    pdfs = glob.glob('./*.pdf')

    # Convert PDF files to formatted text
    for p in pdfs:
        check_output("pdftotext -layout %s" % p, shell=True)
        print("Processed %s" % p)


# Just parse the text files
def do_text():
    # Get text files
    txts = glob.glob('./*.txt')

    # Parse the text files
    (stor, colstats) = parseit(txts)

    # Store the parsed data as CSV
    storeit(stor, colstats)


if __name__ == '__main__':

    # Assumes your current working directory contains the PDFs you wish to use
    # See WARNING at top of script

    # If the 'download' argument was given to this script, download the PDFs
    if len(sys.argv) > 1 and sys.argv[1] == 'download':
        getfiles('https://tax.utah.gov/sales/distribution')
    # If the 'textonly' argument was given, only parse the existing text
    elif len(sys.argv) > 1 and sys.argv[1] == 'textonly':
        do_text()
    # Default: parse PDF files and parse the resulting text
    else:
        do_pdfs()
        do_text()
