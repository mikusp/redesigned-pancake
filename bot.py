#!/usr/bin/env python3

import bisect
import datetime
from dateutil import parser
import discord
from discord import app_commands
from discord.ext import tasks
import pprint
import urllib
import pytz
import validators

from fb import Fb, driver
import io
import json
from collections import namedtuple
import os
import sys
import itertools
from itertools import chain, groupby
from rapidfuzz import fuzz

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import re
import requests

from typing import List, Optional
from zoneinfo import ZoneInfo


OAUTH_TOKEN = os.environ['BOT_TOKEN']
FB_ACCESS_TOKEN = os.environ['FB_TOKEN']
GUILD_ID=int(os.environ['GUILD_ID'])
UPCOMING_EVENTS=int(os.environ['UPCOMING_EVENTS'])
NEW_EVENTS=int(os.environ['NEW_EVENTS'])
ADMIN_ROLE_ID=int(os.environ['ADMIN_ROLE_ID'])
ADMIN_ID=int(os.environ['ADMIN_ID'])
MOD_ROLE_ID=int(os.environ['MOD_ROLE_ID'])
CONTACT_SUBSTITUTIONS="substitutions.json"

os.chdir(sys.path[0])

substitutions = {}
with open(CONTACT_SUBSTITUTIONS) as f:
    try:
        substitutions = json.loads(f.read())
    except e:
        print(e)

tzinfo = ZoneInfo('Europe/London')

class GCal:
    CALENDAR_ID = os.environ['CALENDAR_ID']
    SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

    def __init__(self):
        creds = None
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', GCal.SCOPES)
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', GCal.SCOPES)
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open('token.json', 'w') as token:
                token.write(creds.to_json())

        self.service = build('calendar', 'v3', credentials=creds)

    def fetch_events(self):
        try:
            now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
            week_later = (datetime.datetime.utcnow() + datetime.timedelta(days=7)).isoformat() + 'Z'
            events_result = self.service.events().list(calendarId=GCal.CALENDAR_ID, timeMin=now,
                                                timeMax=week_later, singleEvents=True,
                                                orderBy='startTime').execute()
            events = events_result.get('items', [])

            if not events:
                print('No upcoming events found.')
                return []

            print('fetched events')
            return events

        except HttpError as error:
            print(f'An error occured: {str(error)}')


def json_default(o):
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()

class Event:
    def __init__(self, **kwargs):
        filtered = {k: v for k, v in kwargs.items() if v is not None}
        self.datetime = None
        self._date = None
        self._time = None
        self.author = None
        self.__dict__.update(filtered)
        if isinstance(self.datetime, str):
            self.datetime = datetime.datetime.fromisoformat(self.datetime)
        if isinstance(self._date, str):
            self._date = parser.parse(self._date).date()
        if isinstance(self._time, str):
            self._time = parser.parse(self._time).time()

    @classmethod
    def create(cls, name, **kwargs):
        d = {}
        d['name'] = name
        d['description'] = kwargs.get('description', None)
        d['datetime'] = kwargs.get('datetime', None)
        if isinstance(d['datetime'], str):
            d['datetime'] = datetime.datetime.fromisoformat(d['datetime'])
        d['_date'] = kwargs.get('date', None)
        d['_time'] = kwargs.get('time', None)
        if d['_date'] is None:
            days_until = kwargs.get('days_until', None)
            if days_until:
                d['_date'] = datetime.date.today() + datetime.timedelta(days=days_until)
        d['fb_url'] = kwargs.get('fb_url', None)
        d['gcal_url'] = kwargs.get('gcal_url', None)
        d['email'] = kwargs.get('email', None)
        d['author'] = kwargs.get('discord_author', None)
        d['img'] = kwargs.get('img', None)
        d['location'] = kwargs.get('location', None)
        d['source'] = kwargs.get('source', None)
        d['deleted'] = kwargs.get('deleted', None)

        return cls(**d)

    @property
    def date(self):
        try:
            if self.datetime is not None:
                return self.datetime.date()
            elif self._date is not None:
                return self._date
            else:
                return None
        except AttributeError:
            print(vars(self))
            raise

    # @date.setter
    # def date(self, value):
    #     raise Exception('setting date')

    def approx_datetime(self):
        if self.datetime:
            return self.datetime
        elif self._date:
            tz = pytz.timezone('Europe/London')
            dtime = datetime.datetime(self._date.year, self._date.month, self._date.day)
            return tz.localize(dtime)
        else:
            raise Exception(f'event {self.name} does not have a valid date')

    def delete(self):
        self.deleted = True
        return self

    def active(self):
        try:
            return not self.deleted
        except AttributeError:
            return True
    
    def merge(self, event):
        self.__dict__.update(event.__dict__)
    
    @property
    def time(self):
        if self.datetime:
            return self.datetime.time()
        elif self._time:
            return self._time
        else:
            return None

    @time.setter
    def time(self, value):
        pass

    # @classmethod
    # def from_json(cls, d):
    #     # d = tupl._asdict()
    #     name = d['name']
    #     del d['name']
    #     print('from json')
    #     print(d)
    #     return Event.create(name, **d)
    
    @classmethod
    def from_fbevent(cls, fbevent):
        e = vars(fbevent)
        args = {
            'name': e.get('name'),
            'fb_url': e.get('fb_url', None),
            'id': e.get('id', None),
            'hydrated': e.get('hydrated', None),
            'city': e.get('city', None),
            'location': e.get('location', None),
            'description': e.get('description', None),
            'img': e.get('cover_img_url', None),
            'datetime': e.get('start_time', None),
            'source': 'fb'
        }

        return cls(**args)

    @classmethod
    def from_gcal_event(cls, gcal_event):
        summary = gcal_event['summary']
        dtim = gcal_event['start']['dateTime']
        datetime_parsed = datetime.datetime.strptime(dtim, "%Y-%m-%dT%H:%M:%S%z")
        location = gcal_event['location']
        author = gcal_event['creator']['email']
        url = gcal_event['htmlLink']
        description = gcal_event.get('description', None)

        args = {
            'gcal_url': url,
            'datetime': datetime_parsed,
            'location': location,
            'email': author,
            'description': description,
            'source': 'gcal'
        }

        ev = Event.create(summary, **args)

        return ev

    def to_dict(self):
        dct = vars(self)
        # if '_date' in dct:
        #     dct['date'] = dct['_date']
        #     del dct['_date']
        # if '_time' in dct:
        #     dct['time'] = dct['_time']
        #     del dct['_time']
        return dct

    def selector_value(self):
        d = self.date
        if not d:
            print(vars(self))
        return " - ".join([self.date.isoformat(), self.name[:80]])

    def pretty(self):
        assert self.active, f"{self.name} is deleted"

        organizer = None
        try:
            organizer = self.author if self.author else self.email
        except AttributeError:
            pass

        if organizer is not None and not organizer.startswith('<'):
            try:
                sub = substitutions[organizer]
                organizer = f'[ORGANIZER]({sub})'
            except:
                pass
        
        url = None
        try:
            if hasattr(self, 'url'):
                url = f'[LINK]({self.url}'
            if hasattr(self, 'fb_url'):
                url = f'[FB]({self.fb_url})'
        except AttributeError:
            pass
        
        location = None
        try:
            loc = self.location
            loc = urllib.parse.quote(loc)
            gmaps_link = 'https://www.google.com/maps/search/?api=1&query=' + loc
            location = f'[{self.location}]({gmaps_link})'
        except AttributeError:
            pass

        description = None
        try:
            desc = self.description.replace('<br>', '\n').replace('<br />', '\n')
            description = '```' + desc[0:250] + '```'
        except AttributeError:
            pass

        time = None
        try:
            time = str(self.time) if self.time else 'NO TIME'
        except AttributeError:
            pass
        
        summary = " - ".join([p for p in [time, self.name, url, organizer, location] if p is not None])
        return [p for p in [summary, description] if p is not None]
    
    def summary(self):
        assert self.active, f"{self.name} is deleted"

        organizer = None
        try:
            organizer = self.author if self.author else self.email
        except AttributeError:
            pass

        if organizer is not None and not organizer.startswith('<'):
            try:
                sub = substitutions[organizer]
                organizer = f'[ORGANIZER]({sub})'
            except:
                pass
        
        url = None
        try:
            if hasattr(self, 'url'):
                url = f'[LINK]({self.url}'
            if hasattr(self, 'fb_url'):
                url = f'[FB]({self.fb_url})'
        except AttributeError:
            pass
        
        location = None
        try:
            loc = self.location
            loc = urllib.parse.quote(loc)
            gmaps_link = 'https://www.google.com/maps/search/?api=1&query=' + loc
            location = f"[{self.location.split(',')[0]}]({gmaps_link})"
        except AttributeError:
            pass

        time = None
        try:
            time = str(self.time) if self.time else 'NO TIME'
        except AttributeError:
            pass
        
        summary = " - ".join([p for p in [time, self.name, url, organizer, location] if p is not None])
        return [summary]
    

    def make_embed(self, description_limit=4096) -> discord.Embed:
        description = self.description[0:description_limit].replace('<br>', '\n').replace('<br />', '\n')
        embed = discord.Embed(title=self.name, description=description, url=getattr(self, 'fb_url', None))
        if hasattr(self, 'img'):
            embed.set_image(url=self.img)
        organizer = None
        try:
            organizer = self.author if self.author else self.email
            if organizer is not None and not organizer.startswith('<'):
                try:
                    sub = substitutions[organizer]
                    organizer = sub
                except:
                    pass
            embed.set_author(name=organizer, url=organizer if validators.url(organizer) else None)
        except AttributeError:
            pass
        location = getattr(self, 'location', '').split(',')[0]
        embed.add_field(name='Venue', value=location, inline=True)
        city = getattr(self, 'city', '')
        embed.add_field(name='City', value=city, inline=True)
        embed.add_field(name='Time', value=self.approx_datetime().strftime('%a, %d %b %Y, %H:%M'), inline=True)
        return embed

    def validate(self):
        errors = []
        if not self._date:
            errors.append('date cannot be empty')
        # if not self.name:
        #     errors.append("name cannot be empty")
        # if self.days_until < 0 or self.days_until > 13:
        #     errors.append("date is invalid, please choose one of the options")
        # if not self.url:
        #     errors.append("url cannot be empty")
        
        # try:
        #     response = requests.get(self.url, timeout=10.0)
        #     response.raise_for_status()
        # except requests.exceptions.Timeout:
        #     errors.append("provided url did not respond within 10 seconds")
        # except requests.exceptions.HTTPError as e:
        #     errors.append(f"provided url responded with an error code: {str(e)}")
        # except requests.exceptions.RequestException as e:
        #     errors.append(f"provided url is not correct: {str(e)}")

        return None if not errors else errors

# class GoogleEvent(Event):
#     def __init__(self, name, date, url, author):
#         super().__init__(name, date, url, author)

#     @classmethod
#     def from_json(cls, tupl):
#         if tupl['type'] != '_gcal':
#             return None
#         date = datetime.date.fromisoformat(tupl.date)
#         days_difference = (date - datetime.date.today()).days
#         return cls(tupl.name, days_difference, tupl.url, tupl.author)

class EventValidationException(Exception):
    def __init__(self, errors: List[str]):
        self.msg = ", ".join(errors)

    def __str__(self):
        return self.msg



def dates(current: str):
    def suffix(d):
        return 'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')
    def custom_strftime(format, t):
        return t.strftime(format).replace('{S}', str(t.day) + suffix(t.day))
    
    today = datetime.date.today()
    next_fortnight = [ today + datetime.timedelta(days=x) for x in range(0,13) ]
    next_fortnight_readable = [ custom_strftime('{S} %B', x) for x in next_fortnight ]
    choices = [ app_commands.Choice(name=n, value=i) for i, n in enumerate(next_fortnight_readable) ]
    return [ x for x in choices if current.lower() in x.name.lower() ]

async def date_autocomplete(
    ctx: discord.Interaction,
    current: str
) -> List[app_commands.Choice[int]]:
    autocompleted_dates = dates(current)
    return autocompleted_dates

def eventDecoder(dct):
    return Event(**dct)

class Schedule:
    def __init__(self, events: List[Event]):
        self.events = events

    @classmethod
    async def parse_msg(cls, msg: discord.Message):
        attachment = None
        try:
            attachment = msg.attachments[0]
        except:
            pass

        if not attachment:
            return cls([])
        else:
            file = await attachment.read()
            return cls(Schedule.parse_json(file))
    
    def parse_json(jsonBytes):
        return json.loads(jsonBytes, object_hook=eventDecoder)

    def add_event(self, event: Event):
        potentially_duplicate_events = list(filter(lambda x: event.date == x.date, self.events))
        duplicate_score = list(map(lambda x: (fuzz.token_set_ratio(x.name, event.name), x), potentially_duplicate_events))
        likely_duplicate = list(filter(lambda x: x[0] > 60.0 , sorted(duplicate_score, key=lambda x: x[0])))
        if likely_duplicate:
            duplicate_name = (likely_duplicate[0][1]).name
            index = [i for i, item in enumerate(self.events) if item.name == duplicate_name][0]
            print(f'merging new event {event.name} into {duplicate_name}')
            self.events[index].merge(event)
        else:
            bisect.insort(self.events, event, key=lambda e: e.approx_datetime())
        return self

    def remove_event(self, event_value: str):
        for idx, e in enumerate(self.events):
            if e.selector_value() in event_value:
                del self.events[idx]
                break
        return self

    def dump_json(self):
        return json.dumps([e.to_dict() for e in self.events], default=json_default)

    def merge_gcal(self, gcal_events):
        # should also update existing events in case details changed, sth for later
        existing_gcal_urls = [e.gcal_url for e in self.events if hasattr(e, 'gcal_url')]
        new_events = [e for e in gcal_events if e.gcal_url not in existing_gcal_urls]

        for e in new_events:
            self.add_event(e)

        return self

    def split_post(self, post):
        max_length = 2000
        result = []
        current_string = ""

        for string in post:
            if len(current_string + string) < max_length:
                current_string += string
            else:
                result.append(current_string)
                current_string = string

        if current_string:
            result.append(current_string)

        return result

    def cleanup(self):
        self.events = [e for e in self.events if e.date >= datetime.date.today()]
        return self

    def format_post(self):
        embed_posts = []
        posts = []

        active_events = list(filter(lambda x: x.active(), self.events))

        today = datetime.date.today()
        d = lambda x: today + datetime.timedelta(days=x)
        dates_in_this_week = []
        if today.isoweekday == 1:
            dates_in_this_week = list(itertools.islice(map(d, count(0)), 7))
        else:
            dates_in_this_week = list(itertools.takewhile(lambda x: x.isoweekday() != 1, map(d, itertools.count(0))))

        for date in dates_in_this_week:
            date_events = list(filter(lambda x: x.date == date, active_events))

            embeds = list(map(lambda x: x.make_embed(description_limit=250), date_events))

            msg_content = f"**======== {date.strftime('%A, %B %e')} =======**"
            embed_posts.append((msg_content, embeds))
        
        last_embed_date = dates_in_this_week[-1]
        later_events = list(filter(lambda x: x.date > last_embed_date, active_events))

        for d, evs in groupby(later_events, lambda x: x.date):
            day = []
            day.append(f"**======= {d.strftime('%A, %B %e')} =======**")
            day.append('\n')
            for e in evs:
                day.extend(e.summary())
                day.append('\n')
            posts.append(day)

        # for e in active_events:
        #     print(vars(e))
        # todays_events = list(filter(lambda x: x.date == datetime.date.today(), active_events))
        # tomorrows_events = list(filter(lambda x: x.date == datetime.date.today() + datetime.timedelta(days=1), active_events))
        
        # day = []
        # day.append(f'**======= TODAY\'S EVENTS - {datetime.date.today()} =======**')
        # day.append('\n\n')
        # for e in todays_events:
        #     day.extend(e.pretty())
        #     day.append('\n\n')
        # posts.append(day)

        # day = []
        # day.append(f'**======= TOMORROW\'S EVENTS - {datetime.date.today() + datetime.timedelta(days=1)} =======**')
        # day.append('\n\n')
        # for e in tomorrows_events:
        #     day.extend(e.pretty())
        #     day.append('\n\n')
        # posts.append(day)

        # later_events = list(filter(lambda x: x.date > datetime.date.today() + datetime.timedelta(days=1), active_events))
        # for d, evs in groupby(later_events, lambda x: x.date):
        #     day = []
        #     day.append(f'**======= EVENTS - {d} =======**')
        #     day.append('\n\n')
        #     for e in evs:
        #         day.extend(e.pretty())
        #         day.append('\n\n')
        #     posts.append(day)

        return (embed_posts, list(chain.from_iterable(map(self.split_post, posts))))

async def send_announcement(ctx: discord.Interaction, event: Event):
    channel = client.get_channel(NEW_EVENTS)
    async with channel.typing():
        await channel.send(embed=event.make_embed(), allowed_mentions=discord.AllowedMentions.none())

async def add_event(ctx: discord.Interaction, schedule_message: discord.Message, event: Event):
    schedule = await Schedule.parse_msg(schedule_message)
    schedule.add_event(event)
    await set_events(ctx, schedule_message, schedule)
    await send_announcement(ctx, event)

async def clear_events(ctx: discord.Interaction, schedule_message: discord.Message):
    await set_events(ctx, schedule_message, Schedule([]))

def not_admin(message: discord.Message):
    return message.author.id != ADMIN_ID

async def set_events(ctx, schedule_message: discord.Message, schedule: Schedule):
    js = schedule.dump_json()
    if isinstance(ctx, discord.Interaction):
        ctx = ctx.followup
    posts = schedule.format_post()
    embeds, texts = posts
    pinned_message_content, *other_messages = embeds
    js_file = discord.File(io.BytesIO(js.encode()), spoiler=True, filename='schedule.json')

    channel = schedule_message.channel
    async with channel.typing():
        await channel.purge(check=not_admin)
        sync = await ctx.send(content='.', wait=True)
        msg = await ctx.send(
            content=pinned_message_content[0],
            ephemeral=False,
            file=js_file,
            wait=True,
            embeds=pinned_message_content[1],
            # suppress_embeds=True,
            silent=True,
            allowed_mentions=discord.AllowedMentions.none()
        )
        await msg.pin()
        await sync.delete(delay=1.0)
        for m in other_messages:
            await ctx.send(
                content=m[0],
                # suppress_embeds=True,
                wait=True,
                embeds=m[1],
                silent=True,
                allowed_mentions=discord.AllowedMentions.none()
            )
        for p in texts:
            await ctx.send(
                content=p,
                suppress_embeds=True,
                silent=True,
                allowed_mentions=discord.AllowedMentions.none()
            )

async def remove_event(ctx: discord.Interaction, schedule_message: discord.Message, event_value: str):
    schedule = await Schedule.parse_msg(schedule_message)
    schedule.remove_event(event_value)
    await set_events(ctx, schedule_message, schedule)

async def get_user_events(schedule_message: discord.Message, user: Optional[str]):
    schedule = await Schedule.parse_msg(schedule_message)
    user_events = filter(lambda e: (user is None or e.author == user) and e.active(), schedule.events)
    return list(user_events)

class EventRemovalSelector(discord.ui.Select):
    def __init__(self, user_events: List[Event], response):
        def make_select_option(e: Event) -> discord.SelectOption:
            val = e.selector_value()
            return discord.SelectOption(
                label=val,
                value=val
            )
        options = list(map(make_select_option, user_events))
        dedup_list = []
        for val, vals in itertools.groupby(options, lambda x: x.value):
            foo = list(vals)
            if len(foo) == 1:
                dedup_list.append(foo[0])
            else:
                for i, select in list(zip(itertools.count(1), foo)):
                    dedup_list.append(discord.SelectOption(
                        label=f"{select.label} {i}",
                        value=f"{select.value} {i}"
                    ))

        super().__init__(
            custom_id='user_events',
            placeholder='Choose an event',
            options=dedup_list
        )
        self.response = response

    async def callback(self, ctx: discord.Interaction):
        item = self.values[0]
        # await self.response.delete(delay=10.0)
        await ctx.response.defer(ephemeral=True)
        pinned_message = await pinned_message_in_channel(ctx.channel)
        await remove_event(ctx, pinned_message, item)
        followup = await ctx.followup.send(content=f'Deleted \"{item}\"', ephemeral=True)
        await self.response.delete(delay=5.0)
        await followup.delete(delay=5.0)

class EventRemovalView(discord.ui.View):
    def __init__(self, selector: EventRemovalSelector):
        super().__init__()
        self.add_item(selector)

    async def callback(self, ctx: discord.Interaction):
        await ctx.delete_original_response()

async def pinned_message_in_channel(channel: discord.TextChannel):
    pinned_messages = await channel.pins()
    pinned_message = None
    if not pinned_messages:
        message = await channel.send(content='test')
        await message.pin()
        pinned_message = message
    else:
        pinned_message = pinned_messages[0]

    return pinned_message

gcal = GCal()

def is_bot(ctx):
    return ctx.user.bot

class EventGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name='event')
        self.gcal = gcal
        self.fb = Fb(FB_ACCESS_TOKEN, driver())

    @app_commands.command()
    @app_commands.checks.has_role(ADMIN_ROLE_ID)
    async def sync(self, ctx: discord.Interaction):
        """reserved for admin use"""
        await ctx.response.defer(ephemeral=True)
        gcal_events = self.gcal.fetch_events()
        pinned_message = await pinned_message_in_channel(ctx.channel)
        schedule = await Schedule.parse_msg(pinned_message)
        events = [ Event.from_gcal_event(ev) for ev in gcal_events ]
        # print('creating schedule')
        schedule = schedule.merge_gcal(events)
        await set_events(ctx, pinned_message, schedule)
        followup = await ctx.followup.send(
            ephemeral=True,
            content="event list synced with gcal"
        )
        await followup.delete(delay=5.0)

    @app_commands.command()
    @app_commands.checks.has_role(ADMIN_ROLE_ID)
    async def purge(self, ctx: discord.Interaction):
        """reserved for admin use"""
        await ctx.response.defer(ephemeral=True)
        pinned_message = await pinned_message_in_channel(ctx.channel)
        await clear_events(ctx, pinned_message)
        followup = await ctx.followup.send(
            ephemeral=True,
            content="event list cleared"
        )
        await followup.delete(delay=5.0)

    # @purge.error
    # @sync.error
    # async def purge_error(self, ctx: discord.Interaction, error):
    #     if isinstance(error, app_commands.MissingRole):
    #         await ctx.response.send_message(content='Insufficient permissions', delete_after=10.0, ephemeral=True)
    #     else:
    #         await super().on_error(ctx, error)

    async def get_user_events(self, ctx: discord.Interaction, user: Optional[str]):
        pinned_message = await pinned_message_in_channel(ctx.channel)
        user_events = await get_user_events(pinned_message, user)
        return user_events

    @app_commands.command()
    async def remove(self, ctx: discord.Interaction):
        """Removes an event from the list"""
        await ctx.response.defer(ephemeral=True)
        user_is_mod = MOD_ROLE_ID in list(map(lambda x: x.id, ctx.user.roles))
        print(f'roles: {ctx.user.roles}')
        print(f'user_is_mod: {user_is_mod}')
        user_events = await self.get_user_events(ctx, None if user_is_mod else ctx.user.mention)
        response = await ctx.original_response()
        if not user_events:
            await ctx.edit_original_response(content='You don\'t own any scheduled events')
            await response.delete(delay=5.0)
        else:
            view = EventRemovalView(EventRemovalSelector(user_events[:25], response))
            await ctx.edit_original_response(content='Choose an event to remove', view=view)

    @app_commands.command()
    async def fb(self, ctx: discord.Interaction, url: str):
        """Adds a new event using a FB event link"""
        await ctx.response.defer(ephemeral=True)
        followup = None
        try:
            event = self.fb.event_url(url)
            ev = Event.from_fbevent(event)
            pinned_message = await pinned_message_in_channel(ctx.channel)
            await add_event(ctx, pinned_message, ev)
            followup = await ctx.followup.send(content=f'thank you for adding {url}', ephemeral=True)
        except Exception:
            followup = await ctx.followup.send(
                ephemeral=True,
                content=f"your command has failed, please inform the mods about the issue"
            )
            raise
        finally:
            if followup:
                await followup.delete(delay=60.0)

    @app_commands.command()
    @app_commands.checks.has_role(ADMIN_ROLE_ID)
    async def update(self, ctx: discord.Interaction):
        """reserved for admin use"""
        await update_task()

    @app_commands.command()
    @app_commands.describe(name='Event name')
    @app_commands.describe(date='Event date')
    @app_commands.describe(url='Event url')
    @app_commands.describe(author='Override event organizer (only admin)')
    @app_commands.autocomplete(date=date_autocomplete)
    async def new(self, ctx: discord.Interaction, name: str, date: int, url: str, author: Optional[discord.Member]):
        """Create a new event"""
        event = None
        await ctx.response.defer(ephemeral=True)
        followup = None
        try:
            author_safe = author.mention if ADMIN_ROLE_ID in list(map(lambda x: x.id, ctx.user.roles)) and author is not None else ctx.user.mention
            args = {
                'days_until': date,
                'url': url,
                'discord_author': author_safe
            }
            event = Event.create(name, **args)
            print(vars(event))
            errors = event.validate()
            if errors is not None:
                raise EventValidationException(errors)
            
            pinned_message = await pinned_message_in_channel(ctx.channel)
            await add_event(ctx, pinned_message, event)

            followup = await ctx.followup.send(
                ephemeral=True, 
                content="thank you for adding the event"
                )
        except EventValidationException as e:
            followup = await ctx.followup.send(
                ephemeral=True,
                content=f"your command was unsuccessful because of: {str(e)}"
            )
        except Exception:
            followup = await ctx.followup.send(
                ephemeral=True,
                content=f"your command has failed, please inform the mods about the issue"
            )
            raise
        finally:
            if followup:
                await followup.delete(delay=60.0)

intents = discord.Intents.default()
intents.message_content = True

class CustomClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(self, *args, **kwargs)

    async def setup_hook(self):
        update_task.start()

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@tasks.loop(time=datetime.time(hour=0, minute=1, tzinfo=tzinfo))
async def update_task():
    channel = client.get_channel(UPCOMING_EVENTS)
    print('running update_task')
    
    async with channel.typing():
        pinned_message = await pinned_message_in_channel(channel)

        schedule = await Schedule.parse_msg(pinned_message)
        schedule.cleanup()

        wh = await channel.create_webhook(name='EventBot')

        try:
            gcal_events = gcal.fetch_events()
            events = [ Event.from_gcal_event(ev) for ev in gcal_events ]
            schedule = schedule.merge_gcal(events)

            await set_events(wh, pinned_message, schedule)
        finally:
            await wh.delete()


@client.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print("Ready!")

tree.add_command(EventGroup(), guild=discord.Object(id=GUILD_ID))


if __name__ == "__main__":
    client.run(OAUTH_TOKEN)
