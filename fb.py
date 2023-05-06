from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
import json
import requests
import datetime
from dateutil import parser
import re
import traceback
import pytz


class FbException(Exception):
    pass

def driver():
    options = webdriver.ChromeOptions()
    options.add_extension('extension.crx')
    options.add_argument('--headless=new')

    drv = webdriver.Chrome(options=options)
    drv.implicitly_wait(5)

    return drv

class FbEvent:
    def __init__(self, name, **kwargs):
        self.name = name
        # print(kwargs)
        self.__dict__.update(kwargs)
        # self.location = location.text if isinstance(location, webdriver.remote.webelement.WebElement) else location.get('name', location)
        # self.city = None if isinstance(location, webdriver.remote.webelement.WebElement) else location.get('location', {}).get('city', None)
        # self.start_time = start_time if isinstance(start_time, datetime.datetime) else parser.parse(start_time)

    @classmethod
    def from_json(cls, data):
        name = data.get('name')
        location = data.get('place', {}).get('name', None)
        city = data.get('place', {}).get('location', {}).get('city', None)
        start_time = data.get('start_time', None)
        if start_time is not None:
            start_time = parser.parse(start_time)
        cover_img_url = data.get('cover', {}).get('source', None)
        interested_count = data.get('interested_count', None)
        attending_count = data.get('attending_count', None)
        description = data.get('description', None)
        id = data.get('id')
        fb_url = f'https://www.facebook.com/events/{id}'

        args = {
            'location': location,
            'city': city,
            'start_time': start_time,
            'cover_img_url': cover_img_url,
            'interested_count': interested_count,
            'attending_count': attending_count,
            'description': description,
            'fb_url': fb_url,
            'id': id
        }
        return cls(name, **args)

    @classmethod
    def from_html(cls, data):
        name = data.get('name')
        del data['name']
        return cls(name, **data)

    @classmethod
    def merge(cls, json, html):
        if not json:
            return html
        if not html:
            return json
        
        merged = vars(html)
        merged.update(vars(json))
        name = json.name
        del merged['name']
        merged['hydrated'] = True
        return cls(name, **merged)

class Fb:
    def __init__(self, access_token=None, driver=None):
        self.access_token = access_token
        self.driver = driver

    def json_event(self, event_id):
        if not self.access_token:
            raise FbException('access_token not specified')

        url = f'https://graph.facebook.com/{event_id}?access_token={self.access_token}&fields=description,cover,start_time,place,name,id,interested_count,attending_count,ticket_uri'
        response = requests.get(url)
        event_data = response.json()

        if 'error' in event_data:
            print(f'json error: {event_data}')
            return None
        else:
            event = FbEvent.from_json(event_data)

            return event

    def html_event(self, event_url):
        if not self.driver:
            raise FbException('driver not specified')

        self.driver.get(event_url)
        height = self.driver.execute_script('return document.body.parentNode.scrollHeight')
        self.driver.set_window_size(910, height)

        try:
            more = self.driver.find_element(By.XPATH, '//*[contains(text(),\'See more\')]')
            more.click()
        except:
            pass

        main_div = self.driver.find_element(By.XPATH, '//footer/preceding-sibling::div')
        deets = main_div.find_element(By.XPATH, './/*[contains(text(), \'Details\')]')

        event_info = deets.find_elements(By.XPATH, './../../../../following-sibling::*')

        *info, description = event_info

        dtime = self.driver.find_element(By.XPATH, '//span[contains(text(), \'UTC+0\')]')
        start_dtime = None
        try:
            start_time, end_time = dtime.text.split(' – ', 1)
            start_dtime = parser.parse(start_time)
        except:
            pass
        try:
            start_time, rest = dtime.text.split('-', 1)
            start_dtime = parser.parse(start_time.replace('FROM ',''))
        except:
            pass

        name, location, *rest = dtime.find_elements(By.XPATH, './../following-sibling::*')

        cover_img = None
        try:
            img = self.driver.find_element(By.XPATH, '//img[@data-imgperflogname=\'profileCoverPhoto\']')
            cover_img = img.get_attribute('src')
        except:
            pass

        tz = pytz.timezone('Europe/London')
        
        args = {
            'name': name.text,
            'location': location.text,
            'start_time': tz.localize(start_dtime),
            'cover_img_url': cover_img,
            'description': description.text,
            'fb_url': event_url
        }
        event = FbEvent.from_html(args)

        return event

    def event_url(self, url):
        fb_event_pattern = r"facebook.com/events/"
        event_match = re.search(fb_event_pattern, url)
        event_id_match = re.search(r"\/(\d+)\/?\??[^\?]*$", url)

        if event_match and event_id_match:
            event_id = event_id_match.group(1)
            json_event = None
            html_event = None

            try:
                json_event = self.json_event(event_id)
            except FbException:
                pass
            except Exception as e:
                print(traceback.format_exc())
                print(f'getting fb json exception: {e}')

            try:
                html_event = self.html_event(url)
            except FbException:
                pass
            except Exception as e:
                print(traceback.format_exc())
                print(f'getting fb html exception: {e}')
            
            event = FbEvent.merge(json_event, html_event)
            return event
                
        else:
            raise Exception(f'{url} is not a fb event url')