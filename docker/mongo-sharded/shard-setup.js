// Registers the shards and shards the high-volume collections.
// Runs on every mongos start; each step is skipped when already done.
//
// Shard keys:
//   user_telemetry  { _id: "hashed" }
//       The highest write volume, append-only. A hashed _id spreads inserts
//       over every shard. A ranged ObjectId key would send every insert to
//       the chunk with the highest key: one hot shard.
//   reviews         { restaurant_id: "hashed" }
//       Always read per restaurant, so each query goes to the one shard that
//       holds that restaurant.
// products stays unsharded: small, read-mostly reference data.

const admin = db.getSiblingDB("admin");
admin.auth(process.env.MONGO_USER, process.env.MONGO_PASSWORD);

function retry(what, fn) {
  for (let attempt = 1; ; attempt++) {
    try {
      return fn();
    } catch (e) {
      if (attempt >= 30) throw e;
      print(`${what}: ${e.message}; retrying`);
      sleep(2000);
    }
  }
}

const dbName = process.env.MONGO_DB || "tableflow";
const wanted = (process.env.SHARDS || "").split(/\s+/).filter(Boolean);
const present = admin.runCommand({ listShards: 1 }).shards.map((s) => s._id);

for (const shard of wanted) {
  const name = shard.split("/")[0];
  if (!present.includes(name)) {
    // The shard may still be electing its primary.
    retry(`addShard ${name}`, () => sh.addShard(shard));
    print(`added shard ${name}`);
  }
}

sh.enableSharding(dbName);

const shardKeys = {
  user_telemetry: { _id: "hashed" },
  reviews: { restaurant_id: "hashed" },
};

const config = db.getSiblingDB("config");
for (const [collection, key] of Object.entries(shardKeys)) {
  const ns = `${dbName}.${collection}`;
  if (config.collections.findOne({ _id: ns })) continue;
  // Needed when the collection already has documents.
  db.getSiblingDB(dbName).getCollection(collection).createIndex(key);
  sh.shardCollection(ns, key);
  print(`sharded ${ns} on ${JSON.stringify(key)}`);
}
