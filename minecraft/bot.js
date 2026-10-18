/**
 * 绯木 Minecraft Bot - Node 桥接层 v5
 * 完整版：全量遥测 + 空间感知 + 天气 + 动作控制
 */
const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');
const WebSocket = require('ws');
const mcDataLoader = require('minecraft-data');

const MC_HOST = process.env.MC_HOST || 'localhost';
const MC_PORT = parseInt(process.env.MC_PORT || '25565');
const MC_USERNAME = process.env.MC_USERNAME || 'Feimu';
const MC_VERSION = process.env.MC_VERSION || '1.21.1';
const WS_PORT = 8200;

const MY_NAME = 'EZFM233'; 

let bot = null;
let mcData = null;
const clients = new Set();

function broadcast(obj) {
  const msg = JSON.stringify(obj);
  for (const c of clients) {
    if (c.readyState === 1) {
      try { c.send(msg); } catch (e) {}
    }
  }
}

// ==================== 连接 MC ====================
function createBot() {
  console.log(`[MC] 连接 ${MC_HOST}:${MC_PORT} 版本 ${MC_VERSION} 用户名 ${MC_USERNAME}`);
  bot = mineflayer.createBot({
    host: MC_HOST, port: MC_PORT,
    username: MC_USERNAME, version: MC_VERSION,
    auth: 'offline',
  });

  bot.loadPlugin(pathfinder);

  bot.once('spawn', () => {
    console.log(`[MC] ✅ 已进入世界，位置 ${bot.entity.position}`);
    mcData = mcDataLoader(bot.version);
    broadcast({ type: 'event', event: 'spawn', username: bot.username });

    // ==================== 游戏遥测推送 (防刷屏) ====================
    let lastHealth = 20;
    let lastHurtTime = 0;
    let lastDangerTime = 0;
    let lastPickupTime = 0;

    setInterval(() => {
        if (!bot || !bot.entities) return;
        const player = bot.players[MY_NAME]?.entity;
        if (!player) return;
        
        const currentHealth = player.health !== undefined ? player.health : 20;
        const now = Date.now();
        
        if (currentHealth < lastHealth && (lastHealth - currentHealth) >= 2) {
            if (now - lastHurtTime > 3000) { 
                lastHurtTime = now;
                broadcast({ type: 'event', event: 'health', health: currentHealth });
            }
        }
        lastHealth = currentHealth;
    }, 1000);

    bot.on('playerCollect', (collector, collected) => {
        if (collector.username === MY_NAME) {
            const now = Date.now();
            if (now - lastPickupTime > 5000) {
                lastPickupTime = now;
                let itemName = collected.displayName;
                if (!itemName && collected.name) {
                    const itemData = mcData.itemsByName[collected.name];
                    itemName = itemData ? itemData.displayName : collected.name;
                }
                broadcast({ type: 'event', event: 'pickup', item: itemName || collected.name || '未知物品' });
            }
        }
    });

    bot.on('entityDead', (entity) => {
        if (entity.username === MY_NAME) {
            broadcast({ type: 'event', event: 'death', reason: '环境或怪物' });
            lastHealth = 20; 
        }
    });

    setInterval(() => {
        if (!bot) return;
        const player = bot.players[MY_NAME]?.entity;
        if (!player) return;
        
        const now = Date.now();
        if (now - lastDangerTime < 15000) return; 
        
        const hostile = bot.nearestEntity(e => {
            if (e.type !== 'mob' || e === player) return false;
            const isHostile = e.kind === 'Hostile mobs' || 
                              ['zombie', 'skeleton', 'creeper', 'spider', 'witch', 'enderman'].some(k => e.name.includes(k));
            const dist = e.position.distanceTo(player.position);
            return isHostile && dist < 10;
        });

        if (hostile) {
            lastDangerTime = now;
            broadcast({ type: 'event', event: 'danger', mob: hostile.displayName || hostile.name });
        }
    }, 3000);

    // ==================== ★ 升级版：5秒全量状态与结构推送 ====================
    setInterval(() => {
        if (!bot) return;
        const player = bot.players[MY_NAME]?.entity;
        if (!player) return;

        const isRaining = bot.isRaining;
        const isNight = bot.time.timeOfDay > 13000 && bot.time.timeOfDay < 23000;
        const pos = {
            x: Math.round(player.position.x),
            y: Math.round(player.position.y),
            z: Math.round(player.position.z)
        };

        const mobs = [];
        for (const e of Object.values(bot.entities)) {
            if (e.type === 'mob' && e.position && e.position.distanceTo(player.position) < 8) {
                mobs.push(`${e.displayName || e.name}`);
            }
        }

        let heldItem = '空手';
        if (player.heldItem) {
            heldItem = player.heldItem.displayName || player.heldItem.name;
        }

        // 空间环境感知
        const env = { foot: '未知', front: '未知', special: [] };
        try {
            const blockUnder = bot.blockAt(player.position.offset(0, -1, 0));
            if (blockUnder && blockUnder.name !== 'air') env.foot = blockUnder.displayName || blockUnder.name;

            const blockInFront = bot.blockAtCursor(3); 
            if (blockInFront) env.front = blockInFront.displayName || blockInFront.name;

            const specialIds = mcData.blocksArray
                .filter(b => b.name.includes('chest') || b.name.includes('ore') || b.name.includes('lava') || b.name.includes('water') || b.name.includes('crafting_table') || b.name.includes('furnace') || b.name.includes('door'))
                .map(b => b.id);
                
            const foundBlocks = bot.findBlocks({ matching: specialIds, maxDistance: 6, count: 5 });
            for (const bPos of foundBlocks) {
                const b = bot.blockAt(bPos);
                if (b) {
                    const dist = Math.round(player.position.distanceTo(bPos));
                    env.special.push(`${dist}格外的${b.displayName || b.name}`);
                }
            }
        } catch (e) {}

        broadcast({
            type: 'event',
            event: 'status_update',
            data: { pos, weather: isRaining ? '下雨' : '晴朗', isNight, mobs, heldItem, env }
        });
    }, 5000);
  });

  bot.on('chat', (username, message) => {
    if (username === bot.username) return;
    if (!message || !message.trim()) return;
    if (username === 'Server' || username === '[Server]') return;
    const sysPatterns = [/^Set\s+(the|own|my)/i, /^Teleported\s+/i, /^Gamerule\s+/i, /^\[Server\]/i];
    for (const p of sysPatterns) { if (p.test(message)) return; }
    console.log(`[MC] <${username}> ${message}`);
    broadcast({ type: 'event', event: 'chat', username, message });
  });

  bot.on('kicked', (reason) => broadcast({ type: 'event', event: 'kicked', reason: String(reason) }));
  bot.on('error', (err) => console.log(`[MC] 错误: ${err.message}`));
  bot.on('end', () => {
    console.log(`[MC] 断开连接`);
    broadcast({ type: 'event', event: 'end' });
    bot = null;
  });
}

// ==================== 基础动作 ====================
function resolvePlayer(target) {
  if (!bot) return target;
  if (!target || ['@p', '@a', '@s', '@r', '@e'].includes(target)) {
    const names = Object.keys(bot.players).filter(n => n !== bot.username);
    if (names.length >= 1) return names[0];
  }
  return target;
}

function actionSay(args) { if (!bot) return '未连接'; bot.chat(args.text || ''); return `已发送：${args.text}`; }
function actionFollow(args) {
  if (!bot) return '未连接';
  let target = resolvePlayer(args.target || '');
  const player = bot.players[target];
  if (!player || !player.entity) return `找不到玩家 ${target}`;
  bot.pathfinder.setMovements(new Movements(bot, mcData));
  bot.pathfinder.setGoal(new goals.GoalFollow(player.entity, 2), true);
  return `开始跟随 ${target}`;
}
function actionCome(args) {
  if (!bot) return '未连接';
  let target = resolvePlayer(args.target || '');
  const player = bot.players[target];
  if (!player || !player.entity) return `找不到玩家 ${target}`;
  bot.pathfinder.setMovements(new Movements(bot, mcData));
  bot.pathfinder.setGoal(new goals.GoalNear(player.entity.position.x, player.entity.position.y, player.entity.position.z, 2));
  return `正在走向 ${target}`;
}
function actionStop() { if (bot) bot.pathfinder.setGoal(null); return '已停止移动'; }
function actionStatus() { return '未连接'; }
function actionLook() { return '未连接'; }

const ITEM_NAME_MAP = {
  '石头': 'stone', '圆石': 'cobblestone', '泥土': 'dirt', '沙子': 'sand',
  '煤矿': 'coal_ore', '煤炭': 'coal', '铁矿': 'iron_ore', '粗铁': 'raw_iron',
  '铜矿': 'copper_ore', '粗铜': 'raw_copper', '金矿': 'gold_ore', '粗金': 'raw_gold',
  '红石矿': 'redstone_ore', '红石': 'redstone', '青金石': 'lapis_lazuli', '钻石': 'diamond',
  '绿宝石': 'emerald', '远古残骸': 'ancient_debris',
  '木头': 'oak_log', '橡木': 'oak_log', '白桦': 'birch_log', '云杉': 'spruce_log',
  '木板': 'oak_planks', '铁锭': 'iron_ingot', '金锭': 'gold_ingot', '铜锭': 'copper_ingot',
  '木棍': 'stick', '皮革': 'leather', '线': 'string', '羽毛': 'feather', '骨头': 'bone',
  '火药': 'gunpowder', '纸': 'paper', '书': 'book', '火把': 'torch'
};

function findItemInInventory(inv, name) {
  const mapped_id = ITEM_NAME_MAP[name];
  if (mapped_id) { const item = inv.find(i => i.name === mapped_id); if (item) return { item }; }
  const low = name.toLowerCase();
  let item = inv.find(i => i.name.toLowerCase() === low);
  if (item) return { item };
  for (const [zh, id] of Object.entries(ITEM_NAME_MAP)) {
    if (zh.includes(name) || name.includes(zh)) {
      const it = inv.find(i => i.name === id);
      if (it) return { item: it };
    }
  }
  item = inv.find(i => i.name.toLowerCase().includes(low));
  return { item: item || null };
}

async function actionMine(args) {
  if (!bot) return '未连接';
  const name = (args.block_name || '').trim();
  let en_name = name;
  for (const [zh, en] of Object.entries(ITEM_NAME_MAP)) { if (name.includes(zh)) { en_name = en; break; } }
  const blockInfo = mcData.blocksByName[en_name];
  if (!blockInfo) return `不认识方块 ${name}`;
  bot.pathfinder.setMovements(new Movements(bot, mcData));
  const block = bot.findBlock({ matching: blockInfo.id, maxDistance: 48 });
  if (!block) return `附近没有 ${name}`;
  try {
    await bot.pathfinder.goto(new goals.GoalNear(block.position.x, block.position.y, block.position.z, 2), 10000);
    await bot.dig(block, true);
    return `挖了 1 个 ${name}`;
  } catch (e) { return `挖失败`; }
}

const HOSTILE_MOBS = new Set(['zombie', 'skeleton', 'creeper', 'spider', 'witch', 'enderman']);
async function actionAttack() {
  if (!bot) return '未连接';
  const hostile = Object.values(bot.entities).filter(e => e.type === 'mob' && e.position && HOSTILE_MOBS.has(e.name) && bot.entity.position.distanceTo(e.position) <= 24);
  if (hostile.length === 0) return '附近没有怪物';
  const target = hostile[0];
  bot.pathfinder.setMovements(new Movements(bot, mcData));
  try {
    await bot.pathfinder.goto(new goals.GoalNear(target.position.x, target.position.y, target.position.z, 2), 3000);
    bot.attack(target);
    return `攻击了 ${target.name}`;
  } catch (e) { return `攻击失败`; }
}

async function actionEat() {
  if (!bot) return '未连接';
  if (bot.food >= 20) return '我不饿';
  const item = bot.inventory.items().find(i => i.foodPoints);
  if (!item) return '没食物';
  await bot.equip(item, 'hand'); await bot.consume();
  return `吃过了`;
}

async function actionDrop(args) {
  if (!bot) return '未连接';
  const name = (args.item_name || '').trim();
  const inv = bot.inventory.items();
  const { item } = findItemInInventory(inv, name);
  if (!item) return `没找到"${name}"`;
  try { await bot.toss(item.type, null, Math.min(parseInt(args.count) || 1, item.count)); return `丢了 ${name}`; } catch (e) { return `失败`; }
}

function actionInventory() {
  if (!bot) return '未连接';
  const inv = bot.inventory.items();
  if (inv.length === 0) return '空背包';
  return inv.map(i => `${i.displayName || i.name}×${i.count}`).join('、');
}

const wss = new WebSocket.Server({ port: WS_PORT });
wss.on('connection', (ws) => {
  clients.add(ws);
  ws.on('message', async (raw) => {
    let cmd; try { cmd = JSON.parse(raw); } catch (e) { return; }
    const { id, action, args } = cmd;
    if (!id || !action) return;
    let ok = true, result = '';
    try {
      switch (action) {
        case 'say': result = actionSay(args || {}); break;
        case 'follow': result = actionFollow(args || {}); break;
        case 'come': result = actionCome(args || {}); break;
        case 'stop': result = actionStop(); break;
        case 'status': result = actionStatus(); break;
        case 'look': result = actionLook(); break;
        case 'mine': result = await actionMine(args || {}); break;
        case 'attack': result = await actionAttack(); break;
        case 'eat': result = await actionEat(); break;
        case 'drop': result = await actionDrop(args || {}); break;
        case 'inventory': result = actionInventory(); break;
        default: ok = false; result = `未知指令`;
      }
    } catch (e) { ok = false; result = e.message; }
    ws.send(JSON.stringify({ type: 'response', id, ok, result }));
  });
  ws.on('close', () => clients.delete(ws));
});

createBot();