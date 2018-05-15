#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging, requests, re, time, os, datetime, sqlite3, random, urllib.parse, ast
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from pprint import pprint
from pymongo import MongoClient
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from time import sleep

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = open('token.conf', 'r').read().replace("\n", "")
PROXIES = open('proxy.list', 'r').read().split("\n")
LINK = "https://rarbg.to/torrents.php?search="

client = MongoClient('localhost', 27017)
db = client['rarbg-notify']

hours = [9, 11, 15, 18, 20]

logger = logging.getLogger(__name__)

def help(bot, update):    
    update.message.reply_text('Use /set <name> to insert a new torrent to check\nUse /unset to show the list of your torrent and delete one\nUse /chek to check if there are updates')

def set(bot, update, args, job_queue):
    if len(args) >= 1:
        chat_id = update.message.chat_id
        name = [y for y in [re.sub('[^0-9a-zA-Z]+', '', x.lower()) for x in args] if y]    
        
        user = db.users.find_one({"telegramid": chat_id})
        if user == None:
            user = {
                "telegramid": chat_id,
                "torrentlist": []
            }
            db.users.insert_one(user)

        if name in [x['title'] for x in user['torrentlist']]:
            update.message.reply_text('Torrent is already in list!')
            return

        for h in hours:
            job_queue.run_daily(check, datetime.time(h, 00, 00), context=chat_id, name="{}_{}_{}".format(chat_id, name, h))
        
        torrent = { "title": name, "originalname": " ".join(args), "lastnotify": [] }
        db.users.update_one({"_id": user['_id']}, {'$push': {'torrentlist': torrent}} )

        update.message.reply_text('Torrent successfully set!')
    else:
        update.message.reply_text('Usage: /set <name>')

def newsession():
    s = requests.Session()
    
    ua = UserAgent()
    s.headers.update({'User-Agent': ua.random})
    
    proxy = random.choice(PROXIES)
    proxy = {"http": "http://" + proxy, "https": "http://" + proxy}
    s.proxies.update(proxy)
    
    return s

def now(bot, update, job_queue):
    user = db.users.find_one({"telegramid": update.message.chat_id}) 
    notify = False
    
    session = newsession()
    r = session.get(LINK, allow_redirects=True)
    
    for value in user['torrentlist']:
        sleep(1)
        torrents = scraper(value, session)
        for torrent in torrents:
            notify = True
            description = "Seeders: <b>{}</b> Leechers: <b>{}</b> Size: <b>{}</b>".format(torrent["seeders"], torrent["leechers"], torrent["size"])
            bot.send_message(update.message.chat_id, text="<b>Torrent found:</b>\n{}\n<b>Info:</b>\n{}\n<a href='{}'>Link Torrent</a>".format(torrent['title'].encode("utf-8"), description.encode("utf-8"), torrent['link']), parse_mode="HTML")

            filename = downloadtorrent(torrent, session)
            if not filename is None:
                bot.send_document(update.message.chat_id, document=open(filename, 'rb'))
                os.remove(filename)
                value["lastnotify"].append(torrent['title'])

    if notify is True:
        db.users.update_one({"_id": user['_id']}, {'$set': {'torrentlist': user['torrentlist']}} )
    else:
        bot.send_message(update.message.chat_id, text="Sorry, no new torrent avaiable")

def downloadtorrent(torrent, session):
    args = { 'id': torrent['id'], 'f' : "{}-[rarbg.to].torrent".format(torrent['title']) }
    torrentlink = "https://rarbg.to/download.php?{}".format( urllib.parse.urlencode(args) )
    filerequest = session.get(torrentlink)
    if(filerequest.status_code == 200):
        filename = filerequest.headers.get('Content-Disposition').replace('attachment; filename="', "").replace('"', "")
        with open(filename, 'wb') as f:
            f.write(filerequest.content)
        return filename
    return None    

def scraper(torrentitem, session):
    
    fulllink = LINK +  "+".join(torrentitem['title'])
    print("{} get={}".format(datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S'), fulllink))
    r = session.get(fulllink)
    sleep(0.5)
    
    if r.status_code == 200:
        torrents = []
        soup = BeautifulSoup(r.content, 'html.parser')
        trs = soup.findAll("tr", {"class":"lista2"})
        for tr in trs:
            tds = tr.findAll("td")
            # tds[1] - Title | tds[3] - Size | tds[4] - Seeders | tds[5] - Leechers
            linktorrent = tds[1].find("a", {"onmouseout":"return nd();"})
            title = linktorrent['title']
            if not title in torrentitem["lastnotify"]:
                torrent = {
                    "title": title,
                    "link": "https://rarbg.to" + linktorrent['href'] ,
                    "id": linktorrent['href'].replace('/torrent/', ""),
                    "size": tds[3].text,
                    "seeders": tds[4].text,
                    "leechers":  tds[5].text
                }
                torrents.append(torrent)
    return torrents

def check(bot, job):
    user = db.users.find_one({"telegramid": job.context}) 

    session = newsession()
    r = session.get(LINK, allow_redirects=True)
    
    for value in user['torrentlist']:
        sleep(1)
        torrents = scraper(value, session)
        for torrent in torrents:
            description = "Seeders: <b>{}</b> Leechers: <b>{}</b> Size: <b>{}</b>".format(torrent["seeders"], torrent["leechers"], torrent["size"])
            bot.send_message(job.context, text="<b>Torrent found:</b>\n{}\n<b>Info:</b>\n{}\n<a href='{}'>Link Torrent</a>".format(torrent['title'].encode("utf-8"), description.encode("utf-8"), torrent['link']), parse_mode="HTML")
    
            filename = downloadtorrent(torrent, session)
            if not filename is None:
                bot.send_document(job.context, document=open(filename, 'rb'))
                os.remove(filename)
                value["lastnotify"].append(torrent['title'])

    db.users.update_one({"_id": user['_id']}, {'$set': {'torrentlist': user['torrentlist']}} )

def unset(bot, update):
    user = db.users.find_one({"telegramid": update.message.chat_id}) 
    if user == None or len(user['torrentlist']) == 0:  
        update.message.reply_text("You haven't a torrent. Use /set <name>")
    else: 
        keyboard = []
        for value in user['torrentlist']:
            keyboard.append([InlineKeyboardButton(value["originalname"], callback_data="{}".format(value["title"]) )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text('Select the torrent to delete:', reply_markup=reply_markup)
        

def button(bot, update, job_queue):
    query = update.callback_query

    for h in hours:
        jobs = job_queue.get_jobs_by_name("{}_{}_{}".format(query.message.chat_id, query.data, h))
        for job in jobs:
            job.schedule_removal()

    user = db.users.find_one({"telegramid": query.message.chat_id})
    for value in user['torrentlist']:
        querylist = ast.literal_eval(query.data)
        if value['title'] == querylist:
            torrenttitle = value['originalname'] 
            break
    
    db.users.update_one({"_id": user['_id']}, { '$pull': { 'torrentlist': { 'originalname': torrenttitle} } })

    bot.edit_message_text(text="Torrent delete: {}".format(torrenttitle),
                          chat_id=query.message.chat_id,
                          message_id=query.message.message_id)

def startall(job_queue):
    users = db.users.find({})
    for u in users:
        for value in u['torrentlist']:
            for h in hours:
                job_queue.run_daily(check, datetime.time(h, 00, 00), context=u['telegramid'], name="{}_{}_{}".format(u['telegramid'], value['title'], h))

def error(bot, update, error):
    logger.warn('update={}, error={}'.format(update, error))

if __name__ == '__main__':
    updater = Updater(TOKEN)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", help))
    dp.add_handler(CommandHandler("help", help))
    
    dp.add_handler(CommandHandler('unset', unset))
    dp.add_handler(CallbackQueryHandler(button, pass_job_queue=True))
    
    dp.add_handler(CommandHandler("set", set, pass_args=True, pass_job_queue=True))
    dp.add_handler(CommandHandler("check", now, pass_job_queue=True))
    
    dp.add_error_handler(error)

    startall(updater.job_queue)
    
    updater.start_polling()
    updater.idle()

