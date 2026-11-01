-- A4.5 / I-S4-AN-05: reapply the D8 deletion log to a clean restore.
--
-- `restore_log` is a temporary table created by `backup_restore.sh` with exactly one jsonb
-- document. It carries identifiers and tombstone timestamps only: no transcript, prompt,
-- message content, token or profile is present.
--
-- The order is the same one A4.4 and A2.7 use: a deleted account purges every tenant row and
-- scrubs the user row; a tombstoned class purges its derivatives and content; a tombstoned
-- conversation purges its messages and sources; a tombstoned subject is marked deleted.
-- Running it on a backup taken after the deletion is a no-op, and running it twice produces
-- the same state.

-- 1. Account deletions recorded after the backup are inserted so the residual operation
--    survives and the purge below has one authoritative tenant list. Rows already present
--    in the backup are updated to the newest status/expiry, never duplicated.
INSERT INTO account_deletions AS target
  (id, former_user_id, status, delete_events, expires_at, created_at, completed_at,
   claimed_at, attempts)
SELECT
  COALESCE(x.id, gen_random_uuid()),
  x.former_user_id,
  x.status,
  COALESCE(x.delete_events, false),
  x.expires_at,
  COALESCE(x.created_at, now()),
  x.completed_at,
  NULL,
  0
FROM jsonb_to_recordset(
  COALESCE(
    (SELECT payload -> 'account_deletions' FROM restore_log LIMIT 1),
    '[]'::jsonb
  )
) AS x(
  id uuid,
  former_user_id uuid,
  status text,
  delete_events boolean,
  expires_at timestamptz,
  created_at timestamptz,
  completed_at timestamptz
)
WHERE x.former_user_id IS NOT NULL
  AND x.expires_at IS NOT NULL
  AND x.status IN ('pending', 'running', 'completed', 'completed_with_remote_failures')
ON CONFLICT (former_user_id) DO UPDATE
SET status = EXCLUDED.status,
    delete_events = target.delete_events OR EXCLUDED.delete_events,
    expires_at = GREATEST(target.expires_at, EXCLUDED.expires_at),
    completed_at = COALESCE(EXCLUDED.completed_at, target.completed_at);

-- 2. Deleted accounts: the residual row, the restored user row and the log's `users` array
--    all mean "purge now". Keeping the three sources covers an operation whose residual row
--    already expired while the user tombstone (which has no expiry) still proves the account
--    was deleted, and a `deleted` user whose tombstone was written without an
--    `account_deletions` residual row (for example, a row that predates A4.4 or a manual
--    status change). The log entry supplies the identifier only; the scrub below re-derives
--    the opaque placeholders from the row itself.
CREATE TEMP TABLE restore_deleted_users (
  user_id uuid PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO restore_deleted_users (user_id)
SELECT user_id
FROM (
  SELECT former_user_id AS user_id FROM account_deletions
  UNION
  SELECT id AS user_id FROM users WHERE status <> 'active'
  UNION
  SELECT (entry ->> 'id')::uuid AS user_id
  FROM jsonb_array_elements(
    COALESCE((SELECT payload -> 'users' FROM restore_log LIMIT 1), '[]'::jsonb)
  ) AS entry
  WHERE entry ->> 'id' IS NOT NULL
    AND COALESCE(entry ->> 'status', 'deleted') <> 'active'
) AS deleted
ON CONFLICT DO NOTHING;

DELETE FROM message_sources
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM messages
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM conversations
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM notifications
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM task_revisions
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM task_sources
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM calendar_events
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM tasks
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM chunk_segments
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM embeddings
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM chunks
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM segments
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM transcripts
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM processing_runs
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM variant_confirmation_tokens
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM ingestion_attempts
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM audios
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM index_generations
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM subjects
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM outbox_events
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM settings
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM google_credentials
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);
DELETE FROM sessions
WHERE user_id IN (SELECT user_id FROM restore_deleted_users);

UPDATE users
SET status = 'deleted',
    deleted_at = COALESCE(deleted_at, now()),
    email = 'deleted-' || id::text || '@invalid',
    google_sub = 'deleted:' || id::text,
    email_verified = false,
    display_name = NULL,
    timezone = 'UTC'
WHERE id IN (SELECT user_id FROM restore_deleted_users);

-- 3. Tombstoned classes: mark the row and purge every derivative in FK order. The message
--    that cited a purged source keeps the content-free tombstone (never the text).
CREATE TEMP TABLE restore_deleted_audios (
  id uuid NOT NULL,
  user_id uuid NOT NULL,
  deleted_at timestamptz NOT NULL,
  PRIMARY KEY (user_id, id)
) ON COMMIT DROP;
INSERT INTO restore_deleted_audios (id, user_id, deleted_at)
SELECT id, user_id, MIN(deleted_at)
FROM (
  SELECT id, user_id, deleted_at
  FROM audios
  WHERE deleted_at IS NOT NULL
  UNION ALL
  SELECT (entry ->> 'id')::uuid,
         (entry ->> 'user_id')::uuid,
         COALESCE((entry ->> 'deleted_at')::timestamptz, now())
  FROM jsonb_array_elements(
    COALESCE((SELECT payload -> 'audios' FROM restore_log LIMIT 1), '[]'::jsonb)
  ) AS entry
  WHERE entry ->> 'id' IS NOT NULL
    AND entry ->> 'user_id' IS NOT NULL
) AS sources
GROUP BY id, user_id
ON CONFLICT (user_id, id) DO UPDATE
SET deleted_at = LEAST(
  restore_deleted_audios.deleted_at, EXCLUDED.deleted_at
);

UPDATE audios AS audio
SET deleted_at = COALESCE(audio.deleted_at, tombstone.deleted_at, now()),
    active_transcript_version = NULL
FROM restore_deleted_audios AS tombstone
WHERE audio.id = tombstone.id
  AND audio.user_id = tombstone.user_id;

CREATE TEMP TABLE restore_deleted_transcripts (
  id uuid NOT NULL,
  user_id uuid NOT NULL,
  PRIMARY KEY (user_id, id)
) ON COMMIT DROP;
INSERT INTO restore_deleted_transcripts (id, user_id)
SELECT transcript.id, transcript.user_id
FROM transcripts AS transcript
JOIN restore_deleted_audios AS tombstone
  ON tombstone.user_id = transcript.user_id
 AND tombstone.id = transcript.audio_id
ON CONFLICT DO NOTHING;

CREATE TEMP TABLE restore_deleted_chunks (
  id uuid NOT NULL,
  user_id uuid NOT NULL,
  PRIMARY KEY (user_id, id)
) ON COMMIT DROP;
INSERT INTO restore_deleted_chunks (id, user_id)
SELECT chunk.id, chunk.user_id
FROM chunks AS chunk
JOIN restore_deleted_audios AS tombstone
  ON tombstone.user_id = chunk.user_id
 AND tombstone.id = chunk.audio_id
ON CONFLICT DO NOTHING;

CREATE TEMP TABLE restore_deleted_segments (
  id uuid NOT NULL,
  user_id uuid NOT NULL,
  PRIMARY KEY (user_id, id)
) ON COMMIT DROP;
INSERT INTO restore_deleted_segments (id, user_id)
SELECT segment.id, segment.user_id
FROM segments AS segment
JOIN restore_deleted_transcripts AS transcript
  ON transcript.user_id = segment.user_id
 AND transcript.id = segment.transcript_id
ON CONFLICT DO NOTHING;

CREATE TEMP TABLE restore_cited_messages (
  message_id uuid PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO restore_cited_messages (message_id)
SELECT DISTINCT source.message_id
FROM message_sources AS source
WHERE (source.user_id, source.audio_id)
        IN (SELECT user_id, id FROM restore_deleted_audios)
   OR (source.user_id, source.transcript_id)
        IN (SELECT user_id, id FROM restore_deleted_transcripts)
   OR (source.user_id, source.chunk_id)
        IN (SELECT user_id, id FROM restore_deleted_chunks)
   OR (source.user_id, source.segment_id)
        IN (SELECT user_id, id FROM restore_deleted_segments)
ON CONFLICT DO NOTHING;

UPDATE ingestion_attempts
SET status = 'cancelled',
    fencing_token = fencing_token + 1,
    lease_expires_at = NULL
WHERE (user_id, audio_id) IN (SELECT user_id, id FROM restore_deleted_audios)
  AND status IN (
    'awaiting_upload', 'receiving', 'transcribing',
    'transcript_committed_cleanup_pending'
  );

UPDATE outbox_events
SET status = 'skipped'
WHERE user_id IN (SELECT user_id FROM restore_deleted_audios)
  AND resource_type = 'audio'
  AND resource_id IN (SELECT id FROM restore_deleted_audios)
  AND status = 'pending';

UPDATE processing_runs
SET status = 'cancelled',
    finished_at = now()
WHERE (user_id, audio_id) IN (SELECT user_id, id FROM restore_deleted_audios)
  AND status IN ('pending', 'running');

CREATE TEMP TABLE restore_audio_tasks (
  id uuid NOT NULL,
  user_id uuid NOT NULL,
  PRIMARY KEY (user_id, id)
) ON COMMIT DROP;
INSERT INTO restore_audio_tasks (id, user_id)
SELECT task.id, task.user_id
FROM tasks AS task
JOIN restore_deleted_audios AS tombstone
  ON tombstone.user_id = task.user_id
 AND tombstone.id = task.audio_id
ON CONFLICT DO NOTHING;

DELETE FROM task_revisions
WHERE (user_id, task_id) IN (SELECT user_id, id FROM restore_audio_tasks);
DELETE FROM task_sources
WHERE (user_id, task_id) IN (SELECT user_id, id FROM restore_audio_tasks)
   OR (user_id, audio_id) IN (SELECT user_id, id FROM restore_deleted_audios);
DELETE FROM tasks
WHERE (user_id, id) IN (SELECT user_id, id FROM restore_audio_tasks);

DELETE FROM message_sources
WHERE (user_id, audio_id) IN (SELECT user_id, id FROM restore_deleted_audios)
   OR (user_id, transcript_id) IN (SELECT user_id, id FROM restore_deleted_transcripts)
   OR (user_id, chunk_id) IN (SELECT user_id, id FROM restore_deleted_chunks)
   OR (user_id, segment_id) IN (SELECT user_id, id FROM restore_deleted_segments);

UPDATE messages AS message
SET content = '',
    citas = '[]'::jsonb
WHERE message.id IN (SELECT message_id FROM restore_cited_messages)
   OR (message.user_id, message.conversation_id) IN (
     SELECT conversation.user_id, conversation.id
     FROM conversations AS conversation
     WHERE conversation.mode = 'class'
       AND (conversation.user_id, conversation.audio_id)
             IN (SELECT user_id, id FROM restore_deleted_audios)
   );

DELETE FROM embeddings
WHERE (user_id, chunk_id) IN (SELECT user_id, id FROM restore_deleted_chunks);
DELETE FROM chunk_segments
WHERE (user_id, chunk_id) IN (SELECT user_id, id FROM restore_deleted_chunks);
DELETE FROM chunks
WHERE (user_id, id) IN (SELECT user_id, id FROM restore_deleted_chunks);
DELETE FROM segments
WHERE (user_id, id) IN (SELECT user_id, id FROM restore_deleted_segments);
DELETE FROM transcripts
WHERE (user_id, id) IN (SELECT user_id, id FROM restore_deleted_transcripts);

-- 4. Tombstoned subjects: preserve the tombstone. A subject can only be deleted after its
--    classes are tombstoned, so the class purge above has already removed their corpus.
CREATE TEMP TABLE restore_deleted_subjects (
  id uuid NOT NULL,
  user_id uuid NOT NULL,
  deleted_at timestamptz NOT NULL,
  PRIMARY KEY (user_id, id)
) ON COMMIT DROP;
INSERT INTO restore_deleted_subjects (id, user_id, deleted_at)
SELECT id, user_id, MIN(deleted_at)
FROM (
  SELECT id, user_id, deleted_at
  FROM subjects
  WHERE deleted_at IS NOT NULL
  UNION ALL
  SELECT (entry ->> 'id')::uuid,
         (entry ->> 'user_id')::uuid,
         COALESCE((entry ->> 'deleted_at')::timestamptz, now())
  FROM jsonb_array_elements(
    COALESCE((SELECT payload -> 'subjects' FROM restore_log LIMIT 1), '[]'::jsonb)
  ) AS entry
  WHERE entry ->> 'id' IS NOT NULL
    AND entry ->> 'user_id' IS NOT NULL
) AS sources
GROUP BY id, user_id
ON CONFLICT (user_id, id) DO UPDATE
SET deleted_at = LEAST(
  restore_deleted_subjects.deleted_at, EXCLUDED.deleted_at
);

UPDATE subjects AS subject
SET deleted_at = COALESCE(subject.deleted_at, tombstone.deleted_at, now())
FROM restore_deleted_subjects AS tombstone
WHERE subject.id = tombstone.id
  AND subject.user_id = tombstone.user_id;

-- 5. Tombstoned conversations: clear the title (it is content) and purge messages/sources.
CREATE TEMP TABLE restore_deleted_conversations (
  id uuid NOT NULL,
  user_id uuid NOT NULL,
  deleted_at timestamptz NOT NULL,
  PRIMARY KEY (user_id, id)
) ON COMMIT DROP;
INSERT INTO restore_deleted_conversations (id, user_id, deleted_at)
SELECT id, user_id, MIN(deleted_at)
FROM (
  SELECT id, user_id, deleted_at
  FROM conversations
  WHERE deleted_at IS NOT NULL
  UNION ALL
  SELECT (entry ->> 'id')::uuid,
         (entry ->> 'user_id')::uuid,
         COALESCE((entry ->> 'deleted_at')::timestamptz, now())
  FROM jsonb_array_elements(
    COALESCE(
      (SELECT payload -> 'conversations' FROM restore_log LIMIT 1),
      '[]'::jsonb
    )
  ) AS entry
  WHERE entry ->> 'id' IS NOT NULL
    AND entry ->> 'user_id' IS NOT NULL
) AS sources
GROUP BY id, user_id
ON CONFLICT (user_id, id) DO UPDATE
SET deleted_at = LEAST(
  restore_deleted_conversations.deleted_at, EXCLUDED.deleted_at
);

UPDATE conversations AS conversation
SET deleted_at = COALESCE(conversation.deleted_at, tombstone.deleted_at, now()),
    updated_at = now(),
    title = NULL
FROM restore_deleted_conversations AS tombstone
WHERE conversation.id = tombstone.id
  AND conversation.user_id = tombstone.user_id;

DELETE FROM message_sources
WHERE (user_id, message_id) IN (
  SELECT message.user_id, message.id
  FROM messages AS message
  JOIN restore_deleted_conversations AS tombstone
    ON tombstone.user_id = message.user_id
   AND tombstone.id = message.conversation_id
);
DELETE FROM messages
WHERE (user_id, conversation_id) IN (
  SELECT user_id, id FROM restore_deleted_conversations
);
