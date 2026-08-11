前提摘要：
　　當前狀態在本機做了STT測試，透過cyberon_stt_client.py去找遠端STTserver跑STT模型，api_server由別人負責，但也需要你幫忙產出相關對應內容，STT與TTS模型無法隨意更動邏輯，須按照他們的邏輯做串接。
　　
各文件關鍵字：
	NLP : 110LLM
	STT & TTS : cyberon
	SQL : MySQL
	PBX : Asterisk
補充：

　　NLP與STT/TTS皆在同一台設備，asterisk與api_server為同一台設備，前端主系統為獨立一台設備。
　　所有資料間的傳輸溝通都以uuid作為唯一值辨別。
　　
那麼接下來的目標是想做到
1. 從本機asterisk，抓到音訊後透過目前的方式將音訊送去遠端STT模型跑辨識結果，再把結果丟到api server送到資料庫，送結果的同時必須連帶此channel的uuid一併送給api_server。
問題：辨識結果應該在STT設備產出還是在asterisk設備產出?
2.  api_server將結果存到資料庫後建立一筆資料並將uuid當作唯一值，同時將stt辨識結果送到NLP模型
3. NLP將對應結果送到資料庫後同時再透過api_server送到TTS將結果轉成語音。
4. TTS產生的語音再傳回給asterisk播放
問題：此語音該使用音檔還是即時音訊?
目前先到這一步
希望能夠一步一步做到，測試完成後在進行下一步，不要一次做完
