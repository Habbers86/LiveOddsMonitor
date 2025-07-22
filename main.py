import requests
from datetime import datetime
import time
import sys
import json
import os
import logging
from flask import Flask, request

app = Flask(__name__)

class LiveOddsMonitor:
    def __init__(self):
        # Настройки API
        self.API_KEY = "k7341tvgylhoekwn"
        self.BASE_URL = "https://api.sstats.net"
        self.TIMEZONE = 3
        self.PREFERRED_BOOKMAKER_ID = 8  # Bet365
        self.MAX_DIFFERENCE = 2.0
        self.MIN_CHANGE = 0.1
        self.CHECK_INTERVAL = 60
        self.DEBUG_MODE = True
        self.LOG_FILE = "matches_log.json"
        
        # Telegram
        self.TELEGRAM_TOKEN = "7816438293:AAFMT4tP5yc81cDczATVlAG3zeXVlrGFmHs"
        self.TELEGRAM_CHAT_ID = "-1002751995841"

        # Состояние
        self.last_scan_ids = set()
        self.processed_matches = set()
        self.stats = {
            'total_processed': 0,
            'found_balanced': 0,
            'last_scan_time': None,
            'last_scan_count': 0
        }

        # Логирование
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)

        # Загрузка состояния
        self.load_state()

    def load_state(self):
        if os.path.exists(self.LOG_FILE):
            try:
                with open(self.LOG_FILE, 'r') as f:
                    data = json.load(f)
                    self.processed_matches = set(data.get('processed_matches', []))
                    self.last_scan_ids = set(data.get('last_scan_ids', []))
                    self.logger.info(f"✓ Загружено {len(self.processed_matches)} матчей")
            except Exception as e:
                self.logger.error(f"⚠️ Ошибка загрузки: {e}")

    def save_state(self):
        try:
            with open(self.LOG_FILE, 'w') as f:
                json.dump({
                    'processed_matches': list(self.processed_matches),
                    'last_scan_ids': list(self.last_scan_ids)
                }, f)
        except Exception as e:
            self.logger.error(f"⚠️ Ошибка сохранения: {e}")

    def get_new_matches(self, current_matches):
        current_ids = {m['id'] for m in current_matches}
        new_ids = current_ids - self.last_scan_ids
        self.last_scan_ids = current_ids
        return [m for m in current_matches if m['id'] in new_ids and m['id'] not in self.processed_matches]

    def send_telegram_message(self, message):
        url = f"https://api.telegram.org/bot{self.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": self.TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            self.logger.error(f"⚠️ Ошибка Telegram: {e}")

    def get_live_matches(self):
        url = f"{self.BASE_URL}/Games/list"
        params = {
            "TimeZone": self.TIMEZONE,
            "apikey": self.API_KEY,
            "Limit": 1000,
            "Live": True
        }
        try:
            response = requests.get(url, params=params, timeout=20)
            data = response.json()
            return [m for m in data.get('data', []) if isinstance(m, dict) and 'id' in m]
        except Exception as e:
            self.logger.error(f"🚨 Ошибка запроса матчей: {e}")
            return []

    def get_odds_for_match(self, game_id):
        url = f"{self.BASE_URL}/Odds/{game_id}"
        params = {"apikey": self.API_KEY}
        try:
            current = requests.get(url, params=params, timeout=10).json().get('data', [])
            time.sleep(0.3)
            opening = requests.get(url, params={**params, "opening": True}, timeout=10).json().get('data', [])
            time.sleep(0.3)
            return {"current": current, "opening": opening}
        except Exception as e:
            self.logger.error(f"⚠️ Ошибка коэффициентов (ID {game_id}): {e}")
            return None

    def analyze_match(self, match):
        game_id = match.get('id')
        home = match.get('homeTeam', {}).get('name', '?').strip()
        away = match.get('awayTeam', {}).get('name', '?').strip()
        
        odds_data = self.get_odds_for_match(game_id)
        if not odds_data:
            return False
        
        open_odds, bookmaker_id = self.find_main_odds(odds_data['opening'], self.PREFERRED_BOOKMAKER_ID)
        curr_odds, _ = self.find_main_odds(odds_data['current'], self.PREFERRED_BOOKMAKER_ID)
        
        if not open_odds or not curr_odds:
            open_odds, bookmaker_id = self.find_main_odds(odds_data['opening'])
            curr_odds, _ = self.find_main_odds(odds_data['current'])
            
        if not open_odds or not curr_odds:
            return False
            
        home_diff = self.calculate_percentage_diff(open_odds['1'], curr_odds['1'])
        draw_diff = self.calculate_percentage_diff(open_odds['X'], curr_odds['X'])
        away_diff = self.calculate_percentage_diff(open_odds['2'], curr_odds['2'])
        
        if not self.has_balanced_changes(home_diff, draw_diff, away_diff):
            return False
            
        bookmaker_name = "Bet365" if bookmaker_id == self.PREFERRED_BOOKMAKER_ID else f"БК ID {bookmaker_id}"
        
        if open_odds['1'] < open_odds['2']:
            favorite = home
            favorite_odds = curr_odds['1']
        else:
            favorite = away
            favorite_odds = curr_odds['2']
        
        if favorite_odds <= 1.7:
            bet_type = "фаворит не проиграет"
        else:
            bet_type = "фора +1.5 на фаворита"
        
        message = (
            f"<b>🎯 Сбалансированный лайв матч:</b>\n"
            f"<b>{home} vs {away}</b> ({bookmaker_name})\n\n"
            f"<b>Коэффициенты:</b>\n"
            f"П1: {open_odds['1']} → {curr_odds['1']} (<i>{home_diff:.2f}%</i>)\n"
            f"Ничья: {open_odds['X']} → {curr_odds['X']} (<i>{draw_diff:.2f}%</i>)\n"
            f"П2: {open_odds['2']} → {curr_odds['2']} (<i>{away_diff:.2f}%</i>)\n\n"
            f"<b>Рекомендация:</b> {favorite} ({bet_type})\n"
            f"<i>Макс. расхождение: {self.MAX_DIFFERENCE}%</i>"
        )
        
        self.send_telegram_message(message)
        self.logger.info(f"🔎 Найден матч: {home} vs {away}")
        self.stats['found_balanced'] += 1
        return True

    def process_matches(self):
        try:
            current_matches = self.get_live_matches()
            if not current_matches:
                self.logger.info("ℹ️ Нет активных матчей")
                return
            
            new_matches = self.get_new_matches(current_matches)
            self.logger.info(f"🔍 Проверка | Всего: {len(current_matches)} | Новых: {len(new_matches)}")
            
            self.stats['last_scan_time'] = datetime.now().strftime('%H:%M:%S')
            self.stats['last_scan_count'] = len(current_matches)
            
            if new_matches:
                for match in new_matches:
                    if self.analyze_match(match):
                        self.processed_matches.add(match['id'])
                        self.stats['total_processed'] += 1
                self.save_state()
                
        except Exception as e:
            self.logger.error(f"🚨 Ошибка: {e}")

    def find_main_odds(self, odds_list, preferred_bookmaker=None):
        if not isinstance(odds_list, list):
            return None, None
        
        if preferred_bookmaker:
            for bookmaker in odds_list:
                if isinstance(bookmaker, dict) and bookmaker.get('bookmakerId') == preferred_bookmaker:
                    odds = self.process_bookmaker_odds(bookmaker)
                    if odds:
                        return odds, preferred_bookmaker
        
        for bookmaker in odds_list:
            if isinstance(bookmaker, dict):
                odds = self.process_bookmaker_odds(bookmaker)
                if odds:
                    return odds, bookmaker.get('bookmakerId')
        
        return None, None

    def process_bookmaker_odds(self, bookmaker):
        for market in bookmaker.get('odds', []):
            market_name = market.get('marketName', '').lower()
            if 'winner' in market_name or '1x2' in market_name:
                outcomes = market.get('odds', [])
                if len(outcomes) >= 3:
                    return {
                        '1': float(outcomes[0].get('value', 0)),
                        'X': float(outcomes[1].get('value', 0)),
                        '2': float(outcomes[2].get('value', 0))
                    }
        return None

    def calculate_percentage_diff(self, old, new):
        try:
            return round((new - old) / old * 100, 2)
        except (TypeError, ZeroDivisionError):
            return 0.0

    def has_balanced_changes(self, home_diff, draw_diff, away_diff):
        if home_diff * away_diff > 0:
            return False
        
        if abs(home_diff) < self.MIN_CHANGE and abs(draw_diff) < self.MIN_CHANGE and abs(away_diff) < self.MIN_CHANGE:
            return False
        
        diff1 = abs(abs(home_diff) - abs(away_diff))
        diff2 = abs(abs(home_diff) - abs(draw_diff))
        diff3 = abs(abs(away_diff) - abs(draw_diff))
        
        return (diff1 <= self.MAX_DIFFERENCE and 
                diff2 <= self.MAX_DIFFERENCE and 
                diff3 <= self.MAX_DIFFERENCE)

# Создаем монитор
monitor = LiveOddsMonitor()

@app.route('/')
def home():
    return "LiveOddsMonitor работает! 🚀"

@app.route('/check', methods=['POST'])
def check_matches():
    monitor.process_matches()
    return "OK", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
