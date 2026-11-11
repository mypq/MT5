#property strict
#property version   "1.02"
#property description "GOLD BOT - DEMO-only XAUUSD market data connector"

input string InpBackendBaseUrl = "https://YOUR_BACKEND_DOMAIN";
input string InpSourceId = "MT5-DEMO-01";
input string InpDeviceSecret = "";
input string InpSymbol = "XAUUSD";
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M1;
input int InpHistoryBars = 200;
input int InpSendIntervalSeconds = 60;
input int InpBrokerUtcOffsetMinutes = 0;
input int InpRequestTimeoutMs = 10000;

#define GOLD_BOT_CANDLES_PATH "/api/v1/market-data/candles"
#define GOLD_BOT_MAX_HISTORY 2000
#define GOLD_BOT_HMAC_BLOCK_SIZE 64

long g_nonce_counter = 0;
int g_symbol_digits = 2;
string g_backend_url = "";

//+------------------------------------------------------------------+
//| Convert text to UTF-8 bytes                                      |
//+------------------------------------------------------------------+
bool StringToUtf8Bytes(const string value, uchar &output[])
{
   ArrayResize(output, 0);

   int count = StringToCharArray(
      value,
      output,
      0,
      StringLen(value),
      CP_UTF8
   );

   return count > 0 && ArraySize(output) == count;
}

//+------------------------------------------------------------------+
//| Convert bytes to lowercase hexadecimal                           |
//+------------------------------------------------------------------+
string BytesToHex(const uchar &data[], const int count)
{
   if(count < 0 || count > ArraySize(data))
      return "";

   string result = "";

   for(int i = 0; i < count; i++)
      result += StringFormat("%02x", (int)data[i]);

   return result;
}

//+------------------------------------------------------------------+
//| Calculate SHA-256                                                |
//+------------------------------------------------------------------+
bool CalculateSha256(const uchar &data[], uchar &hash[])
{
   uchar empty_key[];

   ArrayResize(empty_key, 0);
   ArrayResize(hash, 0);

   int result = CryptEncode(
      CRYPT_HASH_SHA256,
      data,
      empty_key,
      hash
   );

   return result == 32 && ArraySize(hash) == 32;
}

//+------------------------------------------------------------------+
//| Calculate HMAC-SHA256                                            |
//+------------------------------------------------------------------+
bool CalculateHmacSha256(
   const uchar &secret[],
   const uchar &message[],
   uchar &signature[]
)
{
   if(ArraySize(secret) < 32)
      return false;

   uchar key_block[];
   ArrayResize(key_block, GOLD_BOT_HMAC_BLOCK_SIZE);
   ArrayInitialize(key_block, 0);

   if(ArraySize(secret) > GOLD_BOT_HMAC_BLOCK_SIZE)
   {
      uchar shortened_key[];

      if(!CalculateSha256(secret, shortened_key))
         return false;

      ArrayCopy(
         key_block,
         shortened_key,
         0,
         0,
         ArraySize(shortened_key)
      );
   }
   else
   {
      ArrayCopy(
         key_block,
         secret,
         0,
         0,
         ArraySize(secret)
      );
   }

   uchar inner_pad[];
   uchar outer_pad[];

   ArrayResize(inner_pad, GOLD_BOT_HMAC_BLOCK_SIZE);
   ArrayResize(outer_pad, GOLD_BOT_HMAC_BLOCK_SIZE);

   for(int i = 0; i < GOLD_BOT_HMAC_BLOCK_SIZE; i++)
   {
      inner_pad[i] = (uchar)(key_block[i] ^ 0x36);
      outer_pad[i] = (uchar)(key_block[i] ^ 0x5C);
   }

   int message_size = ArraySize(message);

   uchar inner_data[];

   ArrayResize(
      inner_data,
      GOLD_BOT_HMAC_BLOCK_SIZE + message_size
   );

   ArrayCopy(
      inner_data,
      inner_pad,
      0,
      0,
      GOLD_BOT_HMAC_BLOCK_SIZE
   );

   if(message_size > 0)
   {
      ArrayCopy(
         inner_data,
         message,
         GOLD_BOT_HMAC_BLOCK_SIZE,
         0,
         message_size
      );
   }

   uchar inner_hash[];

   if(!CalculateSha256(inner_data, inner_hash))
      return false;

   uchar outer_data[];

   ArrayResize(
      outer_data,
      GOLD_BOT_HMAC_BLOCK_SIZE + ArraySize(inner_hash)
   );

   ArrayCopy(
      outer_data,
      outer_pad,
      0,
      0,
      GOLD_BOT_HMAC_BLOCK_SIZE
   );

   ArrayCopy(
      outer_data,
      inner_hash,
      GOLD_BOT_HMAC_BLOCK_SIZE,
      0,
      ArraySize(inner_hash)
   );

   return CalculateSha256(outer_data, signature);
}

//+------------------------------------------------------------------+
//| Create request token                                             |
//+------------------------------------------------------------------+
string CreateRequestToken(const int requested_length)
{
   g_nonce_counter++;

   string seed =
      IntegerToString((long)TimeGMT()) + "|" +
      IntegerToString((long)GetTickCount()) + "|" +
      IntegerToString((long)GetMicrosecondCount()) + "|" +
      IntegerToString((long)MathRand()) + "|" +
      IntegerToString(g_nonce_counter);

   uchar seed_bytes[];
   uchar hash[];

   if(!StringToUtf8Bytes(seed, seed_bytes))
      return "";

   if(!CalculateSha256(seed_bytes, hash))
      return "";

   string token = BytesToHex(hash, ArraySize(hash));

   if(requested_length <= 0 ||
      requested_length > StringLen(token))
      return "";

   return StringSubstr(token, 0, requested_length);
}

//+------------------------------------------------------------------+
//| Create message identifier                                        |
//+------------------------------------------------------------------+
string CreateMessageId()
{
   string hex = CreateRequestToken(32);

   if(StringLen(hex) != 32)
      return "";

   return
      StringSubstr(hex, 0, 8) + "-" +
      StringSubstr(hex, 8, 4) + "-" +
      StringSubstr(hex, 12, 4) + "-" +
      StringSubstr(hex, 16, 4) + "-" +
      StringSubstr(hex, 20, 12);
}

//+------------------------------------------------------------------+
//| Convert UTC datetime to ISO-8601                                 |
//+------------------------------------------------------------------+
string ToUtcIso8601(const datetime value)
{
   string result = TimeToString(
      value,
      TIME_DATE | TIME_SECONDS
   );

   StringReplace(result, ".", "-");
   StringReplace(result, " ", "T");

   return result + "Z";
}

//+------------------------------------------------------------------+
//| Validate source identifier                                       |
//+------------------------------------------------------------------+
bool IsValidSourceId(const string source_id)
{
   int length = StringLen(source_id);

   if(length < 1 || length > 128)
      return false;

   for(int i = 0; i < length; i++)
   {
      ushort c = StringGetCharacter(source_id, i);

      bool valid =
         (c >= 'A' && c <= 'Z') ||
         (c >= 'a' && c <= 'z') ||
         (c >= '0' && c <= '9') ||
         c == '_' ||
         c == '.' ||
         c == ':' ||
         c == '-';

      if(!valid)
         return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Map supported timeframes                                         |
//+------------------------------------------------------------------+
string GetTimeframeName(const ENUM_TIMEFRAMES timeframe)
{
   switch(timeframe)
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
      case PERIOD_W1:  return "W1";
      case PERIOD_MN1: return "MN1";
   }

   return "";
}

//+------------------------------------------------------------------+
//| Build candle JSON payload                                        |
//+------------------------------------------------------------------+
bool BuildCandlePayload(string &payload, string &message_id)
{
   MqlRates rates[];

   ArraySetAsSeries(rates, true);

   int copied = CopyRates(
      InpSymbol,
      InpTimeframe,
      1,
      InpHistoryBars,
      rates
   );

   if(copied < 2)
   {
      Print(
         "GOLD BOT: Not enough closed candles. Error: ",
         GetLastError()
      );

      return false;
   }

   message_id = CreateMessageId();

   if(StringLen(message_id) != 36)
      return false;

   string timeframe_name = GetTimeframeName(InpTimeframe);

   if(timeframe_name == "")
      return false;

   datetime sent_time = TimeGMT();

   // Correctly escaped JSON string construction.
   payload =
      "{\"message_id\":\"" + message_id +
      "\",\"source_id\":\"" + InpSourceId +
      "\",\"symbol\":\"XAUUSD\"" +
      ",\"timeframe\":\"" + timeframe_name +
      "\",\"sent_at\":\"" + ToUtcIso8601(sent_time) +
      "\",\"volume_source\":\"TICK\"" +
      ",\"candles\":[";

   bool first = true;

   // CopyRates with series indexing: reverse to chronological order.
   for(int i = copied - 1; i >= 0; i--)
   {
      datetime utc_open_time =
         rates[i].time -
         InpBrokerUtcOffsetMinutes * 60;

      if(!first)
         payload += ",";

      first = false;

      payload +=
         "{\"open_time\":\"" +
         ToUtcIso8601(utc_open_time) +
         "\",\"open\":" +
         DoubleToString(rates[i].open, g_symbol_digits) +
         ",\"high\":" +
         DoubleToString(rates[i].high, g_symbol_digits) +
         ",\"low\":" +
         DoubleToString(rates[i].low, g_symbol_digits) +
         ",\"close\":" +
         DoubleToString(rates[i].close, g_symbol_digits) +
         ",\"tick_volume\":" +
         IntegerToString((long)rates[i].tick_volume) +
         ",\"real_volume\":" +
         IntegerToString((long)rates[i].real_volume) +
         ",\"spread_points\":" +
         IntegerToString((long)rates[i].spread) +
         ",\"is_closed\":true}";
   }

   payload += "]}";

   return true;
}

//+------------------------------------------------------------------+
//| Sign exact request bytes                                         |
//+------------------------------------------------------------------+
bool SignRequest(
   const string method,
   const string path,
   const string timestamp,
   const string nonce,
   const uchar &raw_body[],
   string &signature_hex
)
{
   string canonical =
      method + "\n" +
      path + "\n" +
      timestamp + "\n" +
      InpSourceId + "\n" +
      nonce + "\n";

   uchar header_bytes[];

   if(!StringToUtf8Bytes(canonical, header_bytes))
      return false;

   int header_size = ArraySize(header_bytes);
   int body_size = ArraySize(raw_body);

   uchar signed_message[];

   ArrayResize(
      signed_message,
      header_size + body_size
   );

   ArrayCopy(
      signed_message,
      header_bytes,
      0,
      0,
      header_size
   );

   if(body_size > 0)
   {
      ArrayCopy(
         signed_message,
         raw_body,
         header_size,
         0,
         body_size
      );
   }

   uchar secret_bytes[];

   if(!StringToUtf8Bytes(InpDeviceSecret, secret_bytes))
      return false;

   uchar signature[];

   if(!CalculateHmacSha256(
      secret_bytes,
      signed_message,
      signature
   ))
      return false;

   signature_hex = BytesToHex(
      signature,
      ArraySize(signature)
   );

   return StringLen(signature_hex) == 64;
}

//+------------------------------------------------------------------+
//| Send authenticated candle batch                                 |
//+------------------------------------------------------------------+
void SendCandleBatch()
{
   if((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(
      ACCOUNT_TRADE_MODE
   ) != ACCOUNT_TRADE_MODE_DEMO)
   {
      Print("GOLD BOT: DEMO account required. Sending stopped.");
      return;
   }

   string payload;
   string message_id;

   if(!BuildCandlePayload(payload, message_id))
   {
      Print("GOLD BOT: Candle payload could not be created.");
      return;
   }

   uchar raw_body[];

   if(!StringToUtf8Bytes(payload, raw_body))
   {
      Print("GOLD BOT: UTF-8 body conversion failed.");
      return;
   }

   string timestamp = IntegerToString((long)TimeGMT());
   string nonce = CreateRequestToken(32);

   if(StringLen(nonce) != 32)
   {
      Print("GOLD BOT: Request nonce generation failed.");
      return;
   }

   string signature;

   if(!SignRequest(
      "POST",
      GOLD_BOT_CANDLES_PATH,
      timestamp,
      nonce,
      raw_body,
      signature
   ))
   {
      Print("GOLD BOT: Request signature generation failed.");
      return;
   }

   char request_body[];

   int body_size = ArraySize(raw_body);
   ArrayResize(request_body, body_size);

   for(int i = 0; i < body_size; i++)
      request_body[i] = (char)raw_body[i];

   string headers =
      "Content-Type: application/json\r\n" +
      "X-Device-ID: " + InpSourceId + "\r\n" +
      "X-Timestamp: " + timestamp + "\r\n" +
      "X-Nonce: " + nonce + "\r\n" +
      "X-Signature: " + signature + "\r\n";

   string url = g_backend_url + GOLD_BOT_CANDLES_PATH;

   char response[];
   string response_headers;

   ResetLastError();

   int status_code = WebRequest(
      "POST",
      url,
      headers,
      InpRequestTimeoutMs,
      request_body,
      response,
      response_headers
   );

   if(status_code == -1)
   {
      Print(
         "GOLD BOT: WebRequest failed. Error: ",
         GetLastError()
      );

      return;
   }

   if(status_code == 200)
   {
      Print(
         "GOLD BOT: Candle batch accepted. Message ID: ",
         message_id
      );

      return;
   }

   Print(
      "GOLD BOT: Backend rejected candle batch. HTTP status: ",
      status_code,
      ". Response: ",
      CharArrayToString(response, 0, WHOLE_ARRAY, CP_UTF8)
   );
}

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   if(StringFind(InpBackendBaseUrl, "https://") != 0)
   {
      Print("GOLD BOT: HTTPS backend URL is required.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(StringFind(InpBackendBaseUrl, "?") >= 0)
   {
      Print("GOLD BOT: URL query parameters are not allowed.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(StringLen(InpBackendBaseUrl) <= 8)
   {
      Print("GOLD BOT: Backend URL is incomplete.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(!IsValidSourceId(InpSourceId))
   {
      Print("GOLD BOT: Invalid source identifier.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(StringLen(InpDeviceSecret) < 32)
   {
      Print("GOLD BOT: A registered device secret is required.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpSymbol != "XAUUSD" || _Symbol != InpSymbol)
   {
      Print("GOLD BOT: Attach this EA to the exact XAUUSD symbol.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(GetTimeframeName(InpTimeframe) == "")
   {
      Print("GOLD BOT: Unsupported timeframe.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpHistoryBars < 2 ||
      InpHistoryBars > GOLD_BOT_MAX_HISTORY)
   {
      Print("GOLD BOT: History bars must be between 2 and 2000.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpSendIntervalSeconds < 15 ||
      InpSendIntervalSeconds > 3600)
   {
      Print("GOLD BOT: Send interval must be between 15 and 3600.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpBrokerUtcOffsetMinutes < -720 ||
      InpBrokerUtcOffsetMinutes > 840)
   {
      Print("GOLD BOT: Invalid broker UTC offset.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpRequestTimeoutMs < 1000 ||
      InpRequestTimeoutMs > 60000)
   {
      Print("GOLD BOT: Invalid HTTP timeout.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(
      ACCOUNT_TRADE_MODE
   ) != ACCOUNT_TRADE_MODE_DEMO)
   {
      Print("GOLD BOT: DEMO account required.");
      return INIT_FAILED;
   }

   long digits = 0;

   if(!SymbolInfoInteger(
      InpSymbol,
      SYMBOL_DIGITS,
      digits
   ))
   {
      Print("GOLD BOT: Unable to read symbol digits.");
      return INIT_FAILED;
   }

   g_symbol_digits = (int)digits;
   g_backend_url = InpBackendBaseUrl;

   while(
      StringLen(g_backend_url) > 0 &&
      StringSubstr(
         g_backend_url,
         StringLen(g_backend_url) - 1,
         1
      ) == "/"
   )
   {
      g_backend_url = StringSubstr(
         g_backend_url,
         0,
         StringLen(g_backend_url) - 1
      );
   }

   if(StringLen(g_backend_url) <= 8)
   {
      Print("GOLD BOT: Invalid normalized backend URL.");
      return INIT_PARAMETERS_INCORRECT;
   }

   MathSrand((int)GetTickCount());

   if(!EventSetTimer(InpSendIntervalSeconds))
   {
      Print(
         "GOLD BOT: Unable to start timer. Error: ",
         GetLastError()
      );

      return INIT_FAILED;
   }

   Print("GOLD BOT: DEMO-only candle connector initialized.");
   Print("GOLD BOT: No order-placement functions are enabled.");

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Timer event                                                      |
//+------------------------------------------------------------------+
void OnTimer()
{
   SendCandleBatch();
}

//+------------------------------------------------------------------+
//| Tick event                                                       |
//+------------------------------------------------------------------+
void OnTick()
{
   // Transmission is timer-based.
   // No trading operations are performed.
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();

   Print(
      "GOLD BOT: Connector stopped. Reason: ",
      reason
   );
}