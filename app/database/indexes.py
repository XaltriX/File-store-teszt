from pymongo import ASCENDING as A, DESCENDING as D


async def ensure_indexes(db) -> None:
    await db["users"].create_index([("last_active", D)])
    await db["users"].create_index([("blocked", A)])
    await db["files"].create_index([("file_unique_id", A)], unique=True)
    await db["files"].create_index([("status", A), ("created_at", D)])
    await db["files"].create_index([("name_lc", A)])
    await db["links"].create_index([("token", A)], unique=True)
    await db["links"].create_index([("file_id", A), ("status", A)])
    await db["channels"].create_index([("active", A), ("removed", A)])
    await db["flows"].create_index([("user_id", A), ("file_id", A), ("status", A)])
    await db["flows"].create_index([("user_id", A), ("channel_ids", A), ("status", A)])
    await db["flows"].create_index([("expires_at", A)], expireAfterSeconds=0)
    await db["join_requests"].create_index([("chat_id", A), ("user_id", A), ("request_ts", A)], unique=True)
    await db["join_requests"].create_index([("created_at", A)], expireAfterSeconds=30 * 86400)
    await db["broadcast_deliveries"].create_index([("bid", A), ("uid", A)], unique=True)
    await db["broadcasts"].create_index([("status", A)])
    await db["pending_deletes"].create_index([("due", A)])
