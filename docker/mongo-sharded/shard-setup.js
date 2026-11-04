// Creates the admin user, registers the shards and shards the high-volume
// collections. Runs on every mongos start, from more than one mongos at
// once in Kubernetes, so each step checks first and retries on conflict.
//
// Shard keys:
//   user_telemetry  { _id: "hashed" }
//       The highest write volume, append-only. A hashed _id spreads inserts
//       over every shard. A ranged ObjectId key would send every insert to
//       the chunk with the highest key: one hot shard.
//   reviews         { restaurant_id: "hashed" }
//       Always read per restaurant, so each query goes to the one shard that
//       holds that restaurant. Upserts must name restaurant_id too.
// products stays unsharded: small, read-mostly reference data.

function retry(what, fn) {
  for (let attempt = 1; ; attempt++) {
    try {
      return fn();
    } catch (e) {
      if (attempt >= 60) throw e;
      print(`${what}: ${e.message}; retrying`);
      sleep(2000);
    }
  }
}

const admin = db.getSiblingDB("admin");
const user = process.env.MONGO_USER;
const pwd = process.env.MONGO_PASSWORD;

// The first mongos creates the user through the localhost exception. Once
// a user exists the exception closes and createUser answers Unauthorized.
retry("createUser", () => {
  try {
    admin.createUser({ user, pwd, roles: ["root"] });
    print(`created user ${user}`);
  } catch (e) {
    if (e.codeName !== "Unauthorized" && e.code !== 51003 /* user exists */) throw e;
  }
});
retry("auth", () => admin.auth(user, pwd));

const shardIds = () => admin.runCommand({ listShards: 1 }).shards.map((s) => s._id);
for (const shard of (process.env.SHARDS || "").split(/\s+/).filter(Boolean)) {
  const name = shard.split("/")[0];
  // The shard may still be electing its primary.
  retry(`addShard ${name}`, () => {
    if (!shardIds().includes(name)) {
      sh.addShard(shard);
      print(`added shard ${name}`);
    }
  });
}

const dbName = process.env.MONGO_DB || "tableflow";
retry("enableSharding", () => sh.enableSharding(dbName));

const shardKeys = {
  user_telemetry: { _id: "hashed" },
  reviews: { restaurant_id: "hashed" },
};
const config = db.getSiblingDB("config");
for (const [collection, key] of Object.entries(shardKeys)) {
  const ns = `${dbName}.${collection}`;
  retry(`shardCollection ${ns}`, () => {
    if (config.collections.findOne({ _id: ns })) return;
    // Needed when the collection already has documents.
    db.getSiblingDB(dbName).getCollection(collection).createIndex(key);
    sh.shardCollection(ns, key);
    print(`sharded ${ns} on ${JSON.stringify(key)}`);
  });
}
