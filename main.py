import configparser
import datetime
import logging
import os
from urllib.parse import parse_qs
import requests
import smtplib
import time
import urllib3
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

dir_path = os.path.dirname(os.path.realpath(__file__))

config = configparser.ConfigParser()


# Grab all hidden fields
def get_hidden_elements():
    logging.debug('Grabbing hidden elements from %s', config.get('Portal', 'url'))
    resp = urllib3.request('GET', config.get('Portal', 'url'))
    logging.debug('Response: %s', resp.data)
    html_elems = BeautifulSoup(resp.data)
    hidden_tags = html_elems.find_all("input", type="hidden")
    for tag in hidden_tags:
        logging.debug('Hidden tag found. %s = %s', tag['name'], tag['value'])

    return hidden_tags


# Authentication
def auth(hidden_tags, sess):
    params = {
        'txtUsername': config.get('Portal', 'username'),
        'txtPassword': config.get('Portal', 'password'),
        'btnSubmit': 'Sign In',
        'txtEmailSend': ''
    }
    for tag in hidden_tags:
        params[tag['name']] = tag['value']

    logging.debug('Sending request: %s', params)
    resp = sess.post(config.get('Portal', 'url'), data=params)

    logging.info('Auth response code: %s', resp.status_code)

    response_content = resp.content.decode('utf-8')

    if 'TEXT FOR SUCCESS AUTH' in response_content:
        logging.info('Auth success')
    else:
        logging.error('Auth NOT success')
        return None

    return response_content


# Process table data
def process_table(content):
    logging.info('Getting table')
    soup = BeautifulSoup(content)
    table = soup.find(
        lambda tag: tag.name == 'table' and tag.has_attr('data-table') and tag['data-table'] == "record-list")

    rows = table.findAll(lambda tag: tag.name == 'tr')
    logging.info('Total orders count: %i', len(rows))

    all_orders = []
    i = 0

    for row in rows:
        try:
            i = i + 1

            if i > 1:
                a_value = row.findAll('a')[0]
                parsed_url = urlparse("https://test.com/" + a_value.get('href'))

                row_data = {
                    'id': row.findAll('td')[1].string,
                    'url': a_value,
                    'property': row.findAll('td')[2].string,
                    'priority': row.findAll('td')[3].string,
                    'city': row.findAll('td')[4].string,
                    'postal_code': row.findAll('td')[5].string,
                    'category': row.findAll('td')[6].string,
                    'subcategory': row.findAll('td')[7].string,
                    'summary': row.findAll('td')[8].string,
                    'work_href': a_value.get('href'),
                    'work_id': parse_qs(parsed_url.query)['id'][0],
                    'cm': parse_qs(parsed_url.query)['cm'][0],
                    'view_id': parse_qs(parsed_url.query)['viewid'][0]
                }
                logging.info('Row data: %s', row_data)
                all_orders.append(row_data)
        except ValueError:
            logging.warning('Cannot process row. Error: %s. Row (%s)', ValueError, row)

    return all_orders


# Filter data
def search_for_data(orders, sess):
    s_zipcode = config.get('Search', 'zipcode')
    s_category = config.get('Search', 'category')

    logging.info('Total orders: %i', len(orders))
    logging.info('Search defined Postal code (%s) & Category (%s)', s_zipcode, s_category)

    for t in orders:
        if t['postal_code'].startswith(s_zipcode) and s_category in t['category']:
            logging.info('Postal code (%s) & Category matched (%s). Processing...', t['postal_code'], t['category'])
        else:
            logging.info('Postal code (%s) & Category NOT matched (%s). Skipping...', t['postal_code'], t['category'])

    filtered_orders = [x for x in orders if
                       x['postal_code'].startswith(s_zipcode) and s_category in x['category']]
    logging.info('Filtered contents for zipcode (%s) and category (%s): %i', s_zipcode, s_category,
                 len(filtered_orders))

    for work_order in filtered_orders:
        accept_order(work_order, sess)


# Send email
def send_notification(order, accept_result):
    logging.info('Sending email notification')
    mail_content = '''
        Accepting status: %s \r\n\r\n
        ID: %s \r\n
        Property: %s \r\n
        Priority: %s \r\n
        City: %s \r\n
        Postal Code: %s \r\n
        Category: %s \r\n
        Subcategory: %s \r\n\r\n
        Order link: %s \r\n\r\n
        Summary: %s \r\n
        ''' % (
            accept_result, order['id'], order['property'], order['priority'], order['city'], order['postal_code'],
            order['category'], order['subcategory'], order['url'], order['summary'])

    logging.info('mail content %s', mail_content)

    # Setup the MIME
    message = MIMEMultipart()
    message['From'] = config.get('Email', 'from')
    message['To'] = config.get('Email', 'to')
    message['Subject'] = 'New order'

    # The body and the attachments for the mail
    message.attach(MIMEText(mail_content, 'plain'))

    # Create SMTP session for sending the mail
    # use gmail with port
    session = smtplib.SMTP(config.get('SMTP', 'host'), config.get('SMTP', 'port'))

    # enable security
    session.starttls()

    # login with mail_id and password
    session.login(config.get('Email', 'from'), config.get('Email', 'pass'))
    text = message.as_string()
    session.sendmail(config.get('Email', 'from'), config.get('Email', 'to'), text)
    session.quit()
    logging.info('Mail Sent')


# Get vendor ID
def get_vendor_id(work_order, sess):
    url = 'web servis url for getting id'
    fetch_xml = {
        "fetch": "<fetch version='1.0' output-format='xml-platform' mapping='logical' distinct='false'>  bla bla"
                 "</fetch>" % (work_order['work_id'], 'uuid')
    }

    logging.info('Sending Vendor ID command. Payload: %s', fetch_xml)
    resp = sess.post(url, json=fetch_xml)
    logging.info('Vendor ID accepting Response Code: %i', resp.status_code)

    jsonResponse = resp.json()
    logging.info('Vendor ID accepting Response as JSON: %s', jsonResponse)

    return jsonResponse[0]['Columns'][0]['value']


# Accept work order
def accept_order(work_order, sess):
    url = 'accept url'

    vendor_id = get_vendor_id(work_order, sess)

    logging.info('Accepting Work order %s', work_order)
    payload = {
        "Columns": [
            {
                "key": "vendor id",
                "value": vendor_id,
                "id": ""
            }
        ]
    }

    logging.info('Sending Work order accepting command. Payload: %s', payload)
    resp = sess.post(url, json=payload)
    logging.info('Work order accepting Response: %i', resp.status_code)
    send_notification(work_order, resp.status_code)


# Log out
def logout(sess):
    url = "Logout.aspx"
    logging.debug("Logging out ... ")
    resp = sess.get(url)
    logging.debug("Log out. Response: %s", resp.status_code)


# Initialize
def start():
    logging_format = "%(asctime)s: %(levelname)s %(message)s"
    logging.basicConfig(filename='portal.log', format=logging_format, level=logging.DEBUG, datefmt="%H:%M:%S")
    logging.info('Start process... Collecting hidden elements...')

    # Configuration
    config.read('config.ini')

    # Check time
    now = datetime.datetime.now()
    h = now.hour
    d = now.weekday() + 1

    logging.debug('Checking hour. Current hour: %i', h)
    if config.getint('Times', 'start_hour') >= h > config.getint('Times', 'finish_hour'):
        logging.info('This hour (%i) script is not allowed to run. Skipping', h)
        exit(102)

    logging.debug('Checking weekday. Current weekday: %s. Day offs: %s', d, config.get('Times', 'days_off'))
    if str(d) in config.get('Times', 'days_off'):
        logging.info('This weekday (%i) script is not allowed to run. Skipping', d)
        exit(103)

    # Session
    sess = requests.Session()

    # Hidden elements
    hidden_elements = get_hidden_elements()
    logging.info('Authentication...')

    # Authentication
    content = auth(hidden_elements, sess)

    if content is None:
        logging.error('Auth is not success. Please check credentials. ')
        exit(101)

    # Process existing work orders
    orders = process_table(content)
    logging.info('All Orders: %s', orders)

    # Search for data
    search_for_data(orders, sess)

    # Logout
    logout(sess)


# Starting point
if __name__ == '__main__':
    while True:
        try:
            start()
            time.sleep(60)
        except:
            time.sleep(300)
