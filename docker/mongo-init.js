db = db.getSiblingDB("queryx_demo");

db.events.createIndex({ user_id: 1, created_at: -1 });
db.events.insertMany([
  {
    user_id: 1,
    type: "page_view",
    created_at: new Date(),
    properties: { path: "/home", device: "desktop" },
    tags: ["web", "anonymous"]
  },
  {
    user_id: 2,
    type: "purchase",
    created_at: new Date(),
    properties: { amount: 89.5, currency: "EUR" },
    tags: ["checkout"],
    items: [{ sku: "LIFT-001", quantity: 1 }]
  }
]);

db.profiles.createIndex({ email: 1 }, { unique: true });
db.profiles.insertMany([
  {
    email: "ada@example.com",
    preferences: { language: "en", newsletter: true },
    roles: ["admin", "analyst"]
  },
  {
    email: "grace@example.com",
    preferences: { language: "en" },
    roles: ["engineer"]
  }
]);
