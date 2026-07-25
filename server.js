const express = require("express");
const path = require("path");
const fs = require("fs");

const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

/* ─── Data Store ─── */
const DATA_FILE = path.join(__dirname, "data", "blooms.json");

const defaultDays = [
  {day:1, intensity:0},
  {day:2, intensity:2, category:"joy", reflection:"You laughed before you had even had coffee.", context:"Morning · At home"},
  {day:3, intensity:1, category:"calm", reflection:"Today, you found calm in unexpected silence.", context:"Afternoon · Alone"},
  {day:4, intensity:0},
  {day:5, intensity:3, category:"connection", shared:true, reflection:"You both went quiet at the same time, and neither of you reached for a phone.", context:"Evening · Together"},
  {day:6, intensity:0},
  {day:7, intensity:0},
  {day:8, intensity:2, category:"care", reflection:"You called back, even though you were tired.", context:"Evening · Phone call"},
  {day:9, intensity:0},
  {day:10, intensity:2, category:"wonder", reflection:"You stood still longer than you meant to.", context:"Outdoors · Alone"},
  {day:11, intensity:0},
  {day:12, intensity:2, category:"connection", shared:true, reflection:"You spent more time listening than speaking.", context:"Evening · Together"},
  {day:13, intensity:0},
  {day:14, intensity:0},
  {day:15, intensity:1, category:"joy", reflection:"Something small made today lighter.", context:"Midday"},
  {day:16, intensity:0},
  {day:17, intensity:2, category:"calm", reflection:"The morning stayed quiet, and so did you.", context:"Morning · Outdoors"},
  {day:18, intensity:0},
  {day:19, intensity:0},
  {day:20, intensity:3, category:"connection", shared:true, reflection:"Ninety minutes passed and neither of you noticed.", context:"Afternoon · Together"},
  {day:21, intensity:0},
  {day:22, intensity:0},
  {day:23, intensity:1, category:"wonder", reflection:"A song stopped you mid-step.", context:"Commute"},
  {day:24, intensity:0},
  {day:25, intensity:2, category:"care", reflection:"You stayed, without trying to fix anything.", context:"Evening · With a friend"},
  {day:26, intensity:0},
  {day:27, intensity:3, category:"joy", shared:true, reflection:"The conversation ran long, and neither of you minded.", context:"Evening · Together"},
  {day:28, intensity:0}
];

function loadData() {
  try {
    if (fs.existsSync(DATA_FILE)) {
      return JSON.parse(fs.readFileSync(DATA_FILE, "utf8"));
    }
  } catch (e) { /* ignore */ }
  const data = { month: "June", year: 2026, days: defaultDays, reflections: [] };
  saveData(data);
  return data;
}

function saveData(data) {
  fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2), "utf8");
}

let bloomData = loadData();

/* ─── API Routes ─── */

// Get all bloom data
app.get("/api/blooms", (req, res) => {
  res.json(bloomData);
});

// Get a specific day
app.get("/api/blooms/:day", (req, res) => {
  const day = parseInt(req.params.day);
  const entry = bloomData.days.find(d => d.day === day);
  if (!entry) return res.status(404).json({ error: "Day not found" });
  res.json(entry);
});

// Update a day (record a reflection)
app.post("/api/blooms/:day", (req, res) => {
  const day = parseInt(req.params.day);
  let entry = bloomData.days.find(d => d.day === day);
  const { intensity, category, reflection, context, shared } = req.body;
  if (entry) {
    if (intensity !== undefined) entry.intensity = intensity;
    if (category !== undefined) entry.category = category;
    if (reflection !== undefined) entry.reflection = reflection;
    if (context !== undefined) entry.context = context;
    if (shared !== undefined) entry.shared = shared;
  } else {
    entry = { day, intensity: intensity || 0, category, reflection, context, shared };
    bloomData.days.push(entry);
  }
  saveData(bloomData);
  res.json(entry);
});

// Submit a bloom note reflection
app.post("/api/reflect", (req, res) => {
  const { text } = req.body;
  if (!text) return res.status(400).json({ error: "Reflection text required" });
  bloomData.reflections.push({ text, timestamp: new Date().toISOString() });
  saveData(bloomData);
  res.json({ success: true, reflection: text });
});

// Get reflections list
app.get("/api/reflections", (req, res) => {
  res.json(bloomData.reflections);
});

// Catch-all: serve index.html for SPA-like routing
app.get("*", (req, res) => {
  if (req.path.startsWith("/api/")) {
    return res.status(404).json({ error: "API endpoint not found" });
  }
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.listen(PORT, () => {
  console.log("WeBloom server running at http://localhost:" + PORT);
});
