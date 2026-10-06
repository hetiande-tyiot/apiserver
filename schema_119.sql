-- ============================================================
-- 119 消防局 apiserver 資料庫建置
-- 用法（需 sudo，因為 MySQL root 走 auth_socket）：
--   sudo mysql < /home/taiyan/apiserver/schema_119.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS fire119
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 使用者（本機連線用）
-- 本機是 MariaDB：MySQL 的 IDENTIFIED WITH ... BY 語法會報錯。
-- MariaDB 的 IDENTIFIED BY 預設就是 mysql_native_password，pymysql 可直接連。
-- ALTER USER 讓重跑時也會把既有帳號（例如被設成 auth_socket）改回密碼登入。
CREATE USER IF NOT EXISTS 'api119'@'localhost' IDENTIFIED BY 'api119pass';
CREATE USER IF NOT EXISTS 'api119'@'127.0.0.1' IDENTIFIED BY 'api119pass';
ALTER USER 'api119'@'localhost' IDENTIFIED BY 'api119pass';
ALTER USER 'api119'@'127.0.0.1' IDENTIFIED BY 'api119pass';
GRANT ALL PRIVILEGES ON fire119.* TO 'api119'@'localhost';
GRANT ALL PRIVILEGES ON fire119.* TO 'api119'@'127.0.0.1';
FLUSH PRIVILEGES;

USE fire119;

-- 一通電話一筆案件
CREATE TABLE IF NOT EXISTS cases (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  uuid           VARCHAR(64)  NOT NULL UNIQUE,
  channel        VARCHAR(64),
  main_category  VARCHAR(32),           -- 火警 / 救護
  sub_category   VARCHAR(32),           -- 急病 等子類
  address        VARCHAR(255),
  is_ohca        TINYINT(1),            -- 是否判斷為 OHCA
  call_result    VARCHAR(32),           -- dispatched / ohca_transfer / caller_hangup / error
  case_summary   TEXT,
  nlp_reply      TEXT,                  -- 最後一句 AI 回覆
  case_json      JSON,                  -- 119 完整 case（欄位再擴充也不漏資料）
  created_at     DATETIME,
  updated_at     DATETIME,
  ended_at       DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 逐句 STT 紀錄
CREATE TABLE IF NOT EXISTS utterances (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  uuid        VARCHAR(64) NOT NULL,
  role        VARCHAR(16),             -- caller / agent
  stt_text    TEXT,
  created_at  DATETIME,
  INDEX idx_utt_uuid (uuid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 一輪 AI 對話
CREATE TABLE IF NOT EXISTS dialogue (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  uuid        VARCHAR(64) NOT NULL,
  turn        INT,
  stt_text    TEXT,
  nlp_reply   TEXT,
  audio_path  VARCHAR(255),
  created_at  DATETIME,
  INDEX idx_dlg_uuid (uuid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
