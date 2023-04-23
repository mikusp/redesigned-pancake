#!/usr/bin/env python3

import bisect
import datetime
import discord
from discord import app_commands

import io
import json
from collections import namedtuple
import os
import re
import requests

from typing import List, Optional


OAUTH_TOKEN = os.environ['BOT_TOKEN']
FB_ACCESS_TOKEN = os.environ['FB_TOKEN']
GUILD_ID=1097651033947254834
UPCOMING_EVENTS=1097900479842898033
ADMIN_ROLE_ID=1097842120385101854
MOD_ROLE_ID=1097842777699651695

class Fb():
    def __init__(self, access_token):
        self.access_token = access_token

    def event(self, event_id):
        url = f'https://graph.facebook.com/{event_id}?access_token={self.access_token}'
        response = requests.get(url)
        event_data = response.json()
        print(event_data)

        json.dumps(json.loads(event_data))

        return json


    def event_url(self, url):
        pattern = r"/events/(\d+)/?"
        match = re.search(pattern, url)

        if match:
             event_id = match.group(1)
             self.event(event_id)
        else:
             print(f"{url} is not a fb event url")

# class EventBot(discord.Client):
#     def __init__(self, intents):
#         super().__init__(intents=intents)

#     async def handle_message(self, message):
#         print(f"{message.author}: {message.content}")
#         try:
#             pass
#             # self.fb.event_url(message.content)
#         finally:
#             await message.delete()

#     async def on_message(self, message):
#         if message.channel.name == "upcoming-events":
#             await self.handle_message(message)

#     async def on_ready(self):
#         print(f'Logged in as {self.user}')

def json_default(o):
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()

class Event:
    def __init__(self, name, date, url, author):
        self.name = name
        self.days_until = date
        self.date = datetime.date.today() + datetime.timedelta(days=date)
        self.url = url
        self.author = author

    @classmethod
    def from_json(cls, tupl):
        date = datetime.date.fromisoformat(tupl.date)
        days_difference = (date - datetime.date.today()).days
        return cls(tupl.name, days_difference, tupl.url, tupl.author)

    def to_dict(self):
        dct = vars(self)
        del dct["days_until"]
        return dct

    def pretty(self):
        return " - ".join([self.name, self.date.isoformat(), self.author, f"[LINK]({self.url})"])

    def validate(self):
        errors = []
        if not self.name:
            errors.append("name cannot be empty")
        if self.days_until < 0 or self.days_until > 13:
            errors.append("date is invalid, please choose one of the options")
        if not self.url:
            errors.append("url cannot be empty")
        
        try:
            response = requests.get(self.url, timeout=10.0)
            response.raise_for_status()
        except requests.exceptions.Timeout:
            errors.append("provided url did not respond within 10 seconds")
        except requests.exceptions.HTTPError as e:
            errors.append(f"provided url responded with an error code: {str(e)}")
        except requests.exceptions.RequestException as e:
            errors.append(f"provided url is not correct: {str(e)}")

        return None if not errors else errors

class EventValidationException(Exception):
    def __init__(self, errors: List[str]):
        self.msg = ", ".join(errors)

    def __str__(self):
        return self.msg


intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print("Ready!")

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
    return Event.from_json(namedtuple('X', dct)(**dct))

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
        bisect.insort(self.events, event, key=lambda e: e.date)
        return self

    def remove_event(self, event_name: str):
        self.events = [ e for e in self.events if e.name != event_name ]
        return self

    def dump_json(self):
        return json.dumps([e.to_dict() for e in self.events], default=json_default)

    def format_post(self):
        return "\n".join([e.pretty() for e in self.events])

async def add_event(schedule_message: discord.Message, event: Event):
    schedule = await Schedule.parse_msg(schedule_message)
    schedule.add_event(event)
    js = schedule.dump_json()
    new_message = schedule.format_post()
    js_file = discord.File(io.BytesIO(js.encode()), spoiler=True, filename='schedule.json')

    await schedule_message.edit(
        content=new_message,
        attachments=[js_file],
        suppress=True
    )

async def clear_events(schedule_message: discord.Message):
    schedule = Schedule([])
    js = schedule.dump_json()
    new_message = schedule.format_post()
    js_file = discord.File(io.BytesIO(js.encode()), spoiler=True, filename='schedule.json')

    await schedule_message.edit(
        content=new_message,
        attachments=[js_file],
        suppress=True
    )

async def remove_event(schedule_message: discord.Message, event_name: str):
    schedule = await Schedule.parse_msg(schedule_message)
    schedule.remove_event(event_name)
    js = schedule.dump_json()
    new_message = schedule.format_post()
    js_file = discord.File(io.BytesIO(js.encode()), spoiler=True, filename='schedule.json')

    await schedule_message.edit(
        content=new_message,
        attachments=[js_file],
        suppress=True
    )

async def get_user_events(schedule_message: discord.Message, user: Optional[str]):
    schedule = await Schedule.parse_msg(schedule_message)
    user_events = filter(lambda e: user is None or e.author == user, schedule.events)
    return list(user_events)

class EventRemovalSelector(discord.ui.Select):
    def __init__(self, user_events: List[Event], response):
        def make_select_option(e: Event) -> discord.SelectOption:
            return discord.SelectOption(
                label=e.name,
                value=e.name,
                description=e.date.isoformat()
            )
        super().__init__(
            custom_id='user_events',
            placeholder='Choose an event',
            options=list(map(make_select_option, user_events))
        )
        self.response = response

    async def callback(self, ctx: discord.Interaction):
        item = self.values[0]
        # await self.response.delete(delay=10.0)
        await ctx.response.defer(ephemeral=True)
        pinned_message = await EventGroup.pinned_message(ctx)
        await remove_event(pinned_message, item)
        followup = await ctx.followup.send(content=f'Deleted \"{item}\"', ephemeral=True)
        await self.response.delete(delay=5.0)
        await followup.delete(delay=5.0)

class EventRemovalView(discord.ui.View):
    def __init__(self, selector: EventRemovalSelector):
        super().__init__()
        self.add_item(selector)

    async def callback(self, ctx: discord.Interaction):
        await ctx.delete_original_response()

class EventGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name='event')

    async def pinned_message(ctx: discord.Interaction):
        pinned_messages = await ctx.channel.pins()
        pinned_message = None
        if not pinned_messages:
            message = await ctx.channel.send(content='test')
            await message.pin()
            pinned_message = message
        else:
            pinned_message = pinned_messages[0]

        return pinned_message


    @app_commands.command()
    @app_commands.checks.has_role(ADMIN_ROLE_ID)
    async def purge(self, ctx: discord.Interaction):
        """Clears the event list, USE WITH CAUTION"""
        await ctx.response.defer(ephemeral=True)
        pinned_message = await EventGroup.pinned_message(ctx)
        await clear_events(pinned_message)
        followup = await ctx.followup.send(
            ephemeral=True,
            content="event list cleared"
        )
        await followup.delete(delay=5.0)

    async def get_user_events(self, ctx: discord.Interaction, user: Optional[str]):
        pinned_message = await EventGroup.pinned_message(ctx)
        user_events = await get_user_events(pinned_message, user)
        return user_events

    @app_commands.command()
    async def remove(self, ctx: discord.Interaction):
        """Removes an event from the list"""
        await ctx.response.defer(ephemeral=True)
        user_is_mod = MOD_ROLE_ID in ctx.user.roles
        user_events = await self.get_user_events(ctx, None if user_is_mod else ctx.user.mention)
        response = await ctx.original_response()
        view = EventRemovalView(EventRemovalSelector(user_events, response))
        await ctx.edit_original_response(content='Choose an event to remove', view=view)


    @app_commands.command()
    @app_commands.describe(name='Event name')
    @app_commands.describe(date='Event date')
    @app_commands.describe(url='Event url')
    @app_commands.autocomplete(date=date_autocomplete)
    async def new(self, ctx: discord.Interaction, name: str, date: int, url: str):
        """Create a new event"""
        event = None
        await ctx.response.defer(ephemeral=True)
        followup = None
        try:
            event = Event(name, date, url, ctx.user.mention)
            errors = event.validate()
            if errors is not None:
                raise EventValidationException(errors)
            
            pinned_message = await EventGroup.pinned_message(ctx)
            await add_event(pinned_message, event)

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
        finally:
            if followup:
                await followup.delete(delay=60.0)

tree.add_command(EventGroup(), guild=discord.Object(id=GUILD_ID))

if __name__ == "__main__":
    client.run(OAUTH_TOKEN)
