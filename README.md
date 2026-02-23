# 🍔 Дядя Стейк Шоп — WhatsApp Bot

WhatsApp чат-бот для бургерной на Vercel + Upstash Redis + Meta Cloud API.

## Деплой на Vercel

1. Push в GitHub
2. Импорт в Vercel
3. Добавить Environment Variables в Vercel:
   - `WHATSAPP_TOKEN`
   - `WHATSAPP_PHONE_ID` 
   - `VERIFY_TOKEN`
   - `UPSTASH_REDIS_REST_URL`
   - `UPSTASH_REDIS_REST_TOKEN`
4. Настроить Webhook в Meta: `https://your-app.vercel.app/webhook`
