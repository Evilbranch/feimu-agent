/**
 * 绯木 Minecraft Bot - Node 桥接层 v4
 * 完整版：过滤系统消息 + 物品映射 + 智能匹配 + 武器优先 + @p 兜底
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
  });

  bot.on('chat', (username, message) => {
    if (username === bot.username) return;
    if (!message || !message.trim()) return;
    if (username === 'Server' || username === '[Server]') return;
    const sysPatterns = [
      /^Set\s+(the|own|my)/i,
      /^Set\s+own\s+game\s+mode/i,
      /^Set\s+the\s+time/i,
      /^Teleported\s+/i,
      /^Gamerule\s+/i,
      /^Applied\s+/i,
      /^\[Server\]/i,
      /game\s+mode\s+to/i,
      /^Changed\s+the\s+game/i,
      /^Your\s+game\s+mode/i,
      /\bmode\s+to\s+(Creative|Survival|Adventure|Spectator)/i,
    ];
    for (const p of sysPatterns) {
      if (p.test(message)) {
        console.log(`[MC] (系统消息已过滤) ${message}`);
        return;
      }
    }
    console.log(`[MC] <${username}> ${message}`);
    broadcast({ type: 'event', event: 'chat', username, message });
  });

  bot.on('kicked', (reason) => {
    console.log(`[MC] 被踢出: ${reason}`);
    broadcast({ type: 'event', event: 'kicked', reason: String(reason) });
  });

  bot.on('error', (err) => {
    console.log(`[MC] 错误: ${err.message}`);
    broadcast({ type: 'event', event: 'error', message: err.message });
  });

  bot.on('end', () => {
    console.log(`[MC] 断开连接`);
    broadcast({ type: 'event', event: 'end' });
    bot = null;
  });
}

// ==================== 玩家名兜底 ====================
// @p/@a/@s 等 MC 命令选择符，自动替换为唯一的真实玩家
function resolvePlayer(target) {
  if (!bot) return target;
  if (!target || ['@p', '@a', '@s', '@r', '@e'].includes(target)) {
    const names = Object.keys(bot.players).filter(n => n !== bot.username);
    if (names.length >= 1) {
      console.log(`[MC] target=${target || '空'} → 自动改为 ${names[0]}`);
      return names[0];
    }
  }
  return target;
}

// ==================== 基础动作 ====================
function actionSay(args) {
  if (!bot) return '未连接';
  bot.chat(args.text || '');
  return `已在聊天栏发送：${args.text}`;
}

function actionFollow(args) {
  if (!bot) return '未连接';
  let target = resolvePlayer(args.target || '');
  console.log(`[MC] 尝试跟随 ${target}`);
  const player = bot.players[target];
  if (!player || !player.entity) {
    const names = Object.keys(bot.players).filter(n => n !== bot.username);
    return `找不到玩家 ${target}。当前在线玩家：${names.join('、') || '无'}`;
  }
  try {
    const movements = new Movements(bot, mcData);
    movements.canDig = false;
    bot.pathfinder.setMovements(movements);
    bot.pathfinder.setGoal(new goals.GoalFollow(player.entity, 2), true);
    console.log(`[MC] ✅ 开始跟随 ${target}`);
  } catch (e) {
    console.log(`[MC] 跟随失败: ${e.message}`);
    return `跟随失败: ${e.message}`;
  }
  return `开始跟随 ${target}`;
}

function actionCome(args) {
  if (!bot) return '未连接';
  let target = resolvePlayer(args.target || '');
  const player = bot.players[target];
  if (!player || !player.entity) {
    return `找不到玩家 ${target}`;
  }
  const movements = new Movements(bot, mcData);
  bot.pathfinder.setMovements(movements);
  bot.pathfinder.setGoal(new goals.GoalNear(
    player.entity.position.x, player.entity.position.y, player.entity.position.z, 2
  ));
  return `正在走向 ${target}`;
}

function actionStop() {
  if (!bot) return '未连接';
  bot.pathfinder.setGoal(null);
  return '已停止移动';
}

function actionStatus() {
  if (!bot) return '未连接';
  const pos = bot.entity ? bot.entity.position : null;
  const players = Object.keys(bot.players).filter(n => n !== bot.username);
  return `位置 ${pos ? pos.toString() : '?'}，血量 ${bot.health || '?'}，饱食度 ${bot.food || '?'}，在线玩家 ${players.join('、') || '无'}`;
}

function actionLook() {
  if (!bot) return '未连接';
  const pos = bot.entity ? bot.entity.position : null;
  const players = Object.keys(bot.players).filter(n => n !== bot.username);
  const time = bot.time ? (bot.time.timeOfDay < 12000 ? '白天' : '夜晚') : '?';
  return `坐标 X=${pos ? Math.round(pos.x) : '?'} Y=${pos ? Math.round(pos.y) : '?'} Z=${pos ? Math.round(pos.z) : '?'}，时间 ${time}，附近玩家 ${players.join('、') || '无'}`;
}

// ==================== 物品名映射 ====================
const ITEM_NAME_MAP = {
  // 方块
  '石头': 'stone', '原石': 'stone',
  '圆石': 'cobblestone', '石块': 'cobblestone',
  '石砖': 'stone_bricks', '石头砖': 'stone_bricks',
  '苔石': 'mossy_cobblestone', '苔石砖': 'mossy_stone_bricks',
  '深板岩': 'deepslate', '深板岩圆石': 'cobbled_deepslate',
  '花岗岩': 'granite', '闪长岩': 'diorite', '安山岩': 'andesite',
  '泥土': 'dirt', '土': 'dirt', '草方块': 'grass_block', '草径': 'dirt_path',
  '沙子': 'sand', '红沙': 'red_sand', '沙砾': 'gravel', '黏土': 'clay', '粘土': 'clay',
  '砂岩': 'sandstone', '红砂岩': 'red_sandstone',
  '冰': 'ice', '浮冰': 'packed_ice', '蓝冰': 'blue_ice',
  '雪': 'snow_block', '雪块': 'snow_block', '雪球': 'snowball',
  '玻璃': 'glass', '玻璃板': 'glass_pane',
  '黑曜石': 'obsidian', '哭泣的黑曜石': 'crying_obsidian',
  '基岩': 'bedrock',
  '末地石': 'end_stone',
  '下界岩': 'netherrack', '地狱岩': 'netherrack',
  '灵魂沙': 'soul_sand', '灵魂土': 'soul_soil',
  '岩浆块': 'magma_block', '岩浆桶': 'lava_bucket',
  '海绵': 'sponge', '湿海绵': 'wet_sponge',
  '干草块': 'hay_block', '干草': 'hay_block',
  '干海带块': 'dried_kelp_block',
  // 矿石
  '煤矿': 'coal_ore', '煤矿石': 'coal_ore', '煤炭': 'coal', '煤': 'coal',
  '铁矿': 'iron_ore', '铁矿石': 'iron_ore', '粗铁': 'raw_iron',
  '铜矿': 'copper_ore', '铜矿石': 'copper_ore', '粗铜': 'raw_copper',
  '金矿': 'gold_ore', '金矿石': 'gold_ore', '粗金': 'raw_gold',
  '红石矿': 'redstone_ore', '红石矿石': 'redstone_ore', '红石': 'redstone',
  '青金石矿': 'lapis_ore', '青金石': 'lapis_lazuli',
  '钻石矿': 'diamond_ore', '钻石矿石': 'diamond_ore', '钻石': 'diamond',
  '绿宝石矿': 'emerald_ore', '绿宝石': 'emerald',
  '下界石英矿': 'nether_quartz_ore', '石英': 'quartz', '下界石英': 'quartz',
  '远古残骸': 'ancient_debris', '下界合金碎片': 'netherite_scrap',
  // 原木
  '木头': 'oak_log', '橡木': 'oak_log', '橡木原木': 'oak_log',
  '白桦': 'birch_log', '白桦原木': 'birch_log',
  '云杉': 'spruce_log', '云杉原木': 'spruce_log',
  '丛林': 'jungle_log', '丛林原木': 'jungle_log',
  '金合欢': 'acacia_log', '金合欢原木': 'acacia_log',
  '深色橡木': 'dark_oak_log', '深色橡木原木': 'dark_oak_log',
  '红树': 'mangrove_log', '樱花': 'cherry_log', '樱花原木': 'cherry_log',
  '绯红菌柄': 'crimson_stem', '诡异菌柄': 'warped_stem',
  '木板': 'oak_planks', '橡木木板': 'oak_planks', '白桦木板': 'birch_planks',
  '云杉木板': 'spruce_planks', '丛林木板': 'jungle_planks',
  // 工具武器
  '木剑': 'wooden_sword', '石剑': 'stone_sword', '铁剑': 'iron_sword',
  '金剑': 'golden_sword', '钻石剑': 'diamond_sword', '下界合金剑': 'netherite_sword',
  '木镐': 'wooden_pickaxe', '石镐': 'stone_pickaxe', '铁镐': 'iron_pickaxe',
  '金镐': 'golden_pickaxe', '钻石镐': 'diamond_pickaxe', '下界合金镐': 'netherite_pickaxe',
  '木斧': 'wooden_axe', '石斧': 'stone_axe', '铁斧': 'iron_axe',
  '金斧': 'golden_axe', '钻石斧': 'diamond_axe', '下界合金斧': 'netherite_axe',
  '木铲': 'wooden_shovel', '石铲': 'stone_shovel', '铁铲': 'iron_shovel',
  '钻石铲': 'diamond_shovel', '下界合金铲': 'netherite_shovel',
  '木锄': 'wooden_hoe', '石锄': 'stone_hoe', '铁锄': 'iron_hoe',
  '钻石锄': 'diamond_hoe', '下界合金锄': 'netherite_hoe',
  '弓': 'bow', '箭': 'arrow', '十字弓': 'crossbow', '弩': 'crossbow',
  '三叉戟': 'trident', '盾牌': 'shield',
  '钓鱼竿': 'fishing_rod', '打火石': 'flint_and_steel',
  '剪刀': 'shears', '刷子': 'brush', '望远镜': 'spyglass',
  '地图': 'map', '指南针': 'compass', '钟': 'clock',
  '末影珍珠': 'ender_pearl', '末影之眼': 'ender_eye',
  // 盔甲
  '皮革头盔': 'leather_helmet', '皮革胸甲': 'leather_chestplate',
  '皮革护腿': 'leather_leggings', '皮革靴子': 'leather_boots',
  '铁头盔': 'iron_helmet', '铁胸甲': 'iron_chestplate',
  '铁护腿': 'iron_leggings', '铁靴子': 'iron_boots',
  '金头盔': 'golden_helmet', '金胸甲': 'golden_chestplate',
  '金护腿': 'golden_leggings', '金靴子': 'golden_boots',
  '钻石头盔': 'diamond_helmet', '钻石胸甲': 'diamond_chestplate',
  '钻石护腿': 'diamond_leggings', '钻石靴子': 'diamond_boots',
  '下界合金头盔': 'netherite_helmet', '下界合金胸甲': 'netherite_chestplate',
  '下界合金护腿': 'netherite_leggings', '下界合金靴子': 'netherite_boots',
  // 材料
  '铁锭': 'iron_ingot', '铁': 'iron_ingot',
  '金锭': 'gold_ingot', '金': 'gold_ingot',
  '铜锭': 'copper_ingot', '铜': 'copper_ingot',
  '下界合金锭': 'netherite_ingot',
  '木棍': 'stick', '棍子': 'stick',
  '皮革': 'leather', '线': 'string', '羽毛': 'feather',
  '骨头': 'bone', '骨粉': 'bone_meal', '墨囊': 'ink_sac',
  '火药': 'gunpowder', '蜘蛛眼': 'spider_eye',
  '烈焰棒': 'blaze_rod', '烈焰粉': 'blaze_powder',
  '恶魂之泪': 'ghast_tear',
  '岩浆膏': 'magma_cream', '糖': 'sugar',
  '纸': 'paper', '书': 'book', '书与笔': 'writable_book',
  '经验瓶': 'experience_bottle', '附魔之瓶': 'experience_bottle',
  '红石火把': 'redstone_torch', '火把': 'torch', '灵魂火把': 'soul_torch',
  '灯笼': 'lantern', '灵魂灯笼': 'soul_lantern',
  '铁块': 'iron_block', '金块': 'gold_block', '钻石块': 'diamond_block',
  '煤炭块': 'coal_block', '铜块': 'copper_block',
  // 食物
  '苹果': 'apple', '金苹果': 'golden_apple', '附魔金苹果': 'enchanted_golden_apple',
  '面包': 'bread', '饼干': 'cookie', '蛋糕': 'cake', '南瓜派': 'pumpkin_pie',
  '生牛肉': 'beef', '熟牛肉': 'cooked_beef', '牛排': 'cooked_beef',
  '生猪排': 'porkchop', '熟猪排': 'cooked_porkchop', '猪肉': 'porkchop',
  '生鸡肉': 'chicken', '熟鸡肉': 'cooked_chicken', '鸡肉': 'chicken',
  '生羊肉': 'mutton', '熟羊肉': 'cooked_mutton', '羊肉': 'mutton',
  '生兔肉': 'rabbit', '熟兔肉': 'cooked_rabbit', '兔肉': 'rabbit',
  '生鳕鱼': 'cod', '熟鳕鱼': 'cooked_cod', '鳕鱼': 'cod',
  '生鲑鱼': 'salmon', '熟鲑鱼': 'cooked_salmon', '鲑鱼': 'salmon',
  '胡萝卜': 'carrot', '金胡萝卜': 'golden_carrot',
  '土豆': 'potato', '烤土豆': 'baked_potato', '毒土豆': 'poisonous_potato',
  '甜菜': 'beetroot', '甜菜根': 'beetroot',
  '西瓜片': 'melon_slice', '西瓜': 'melon',
  '甜浆果': 'sweet_berries', '发光浆果': 'glow_berries',
  '蘑菇煲': 'mushroom_stew', '兔肉煲': 'rabbit_stew', '甜菜汤': 'beetroot_soup',
  '海带': 'kelp', '干海带': 'dried_kelp', '紫颂果': 'chorus_fruit',
  '热带鱼': 'tropical_fish', '河豚': 'pufferfish',
  // 杂项
  '小麦': 'wheat', '种子': 'wheat_seeds', '小麦种子': 'wheat_seeds',
  '甘蔗': 'sugar_cane', '南瓜': 'pumpkin', '南瓜灯': 'jack_o_lantern',
  '竹子': 'bamboo', '可可豆': 'cocoa_beans', '鸡蛋': 'egg',
  '下界之星': 'nether_star', '龙蛋': 'dragon_egg',
  '海晶碎片': 'prismarine_shard', '海晶砂粒': 'prismarine_crystals',
  '海洋之心': 'heart_of_the_sea', '鹦鹉螺壳': 'nautilus_shell',
  '回响碎片': 'echo_shard', '潜影壳': 'shulker_shell',
};

function findItemInInventory(inv, name) {
  const mapped_id = ITEM_NAME_MAP[name];
  if (mapped_id) {
    const item = inv.find(i => i.name === mapped_id);
    if (item) return { item, how: '精确中文名' };
  }

  const low = name.toLowerCase();
  let item = inv.find(i => i.name.toLowerCase() === low);
  if (item) return { item, how: '英文ID精确' };

  for (const [zh, id] of Object.entries(ITEM_NAME_MAP)) {
    if (zh.includes(name) || name.includes(zh)) {
      const it = inv.find(i => i.name === id);
      if (it) return { item: it, how: `模糊中文(${zh})` };
    }
  }

  item = inv.find(i => i.name.toLowerCase().includes(low));
  if (item) return { item, how: '英文ID模糊' };

  item = inv.find(i => i.displayName && i.displayName.includes(name));
  if (item) return { item, how: '显示名匹配' };

  return { item: null, how: 'none' };
}

// ==================== 挖矿 ====================
async function actionMine(args) {
  if (!bot) return '未连接';
  const name = (args.block_name || '').trim();
  const count = Math.max(1, Math.min(20, parseInt(args.count) || 3));
  if (!name) return '没指定要挖什么';

  let en_name = name;
  for (const [zh, en] of Object.entries(ITEM_NAME_MAP)) {
    if (name.includes(zh)) { en_name = en; break; }
  }

  const blockInfo = mcData.blocksByName[en_name];
  if (!blockInfo) return `不认识方块 ${name}（英文：${en_name}）`;

  const movements = new Movements(bot, mcData);
  movements.canDig = true;
  movements.allow1by1towers = false;
  bot.pathfinder.setMovements(movements);

  let mined = 0;
  const failed = [];

  for (let i = 0; i < count; i++) {
    const block = bot.findBlock({ matching: blockInfo.id, maxDistance: 48 });
    if (!block) { failed.push(`第${i+1}个找不到`); break; }
    try {
      await bot.pathfinder.goto(new goals.GoalNear(
        block.position.x, block.position.y, block.position.z, 2
      ), 10000);
      await bot.dig(block, true);
      mined++;
      console.log(`[MC] 挖到 ${en_name} (${mined}/${count})`);
    } catch (e) {
      console.log(`[MC] 挖失败: ${e.message}`);
      failed.push(e.message);
      break;
    }
  }

  if (mined === 0) return `挖不到 ${name}，${failed[0] || '附近没有'}`;
  return `挖了 ${mined} 个 ${name}`;
}

// ==================== 攻击 ====================
const HOSTILE_MOBS = new Set([
  'zombie', 'skeleton', 'creeper', 'spider', 'cave_spider',
  'enderman', 'witch', 'slime', 'magma_cube', 'blaze', 'ghast',
  'zombie_villager', 'husk', 'stray', 'drowned', 'phantom',
  'pillager', 'vindicator', 'ravager', 'evoker', 'vex', 'illusioner',
  'piglin_brute', 'hoglin', 'zoglin', 'warden', 'silverfish',
  'endermite', 'guardian', 'elder_guardian', 'shulker',
  'wither_skeleton', 'bogged', 'breeze',
]);

const WEAPON_PRIORITY = [
  'netherite_sword', 'diamond_sword', 'iron_sword',
  'stone_sword', 'golden_sword', 'wooden_sword',
  'netherite_axe', 'diamond_axe', 'iron_axe',
  'stone_axe', 'golden_axe', 'wooden_axe',
];

async function equipBestWeapon() {
  const inv = bot.inventory.items();
  for (const w of WEAPON_PRIORITY) {
    const item = inv.find(i => i.name === w);
    if (item) {
      try {
        await bot.equip(item, 'hand');
        console.log(`[MC] 已装备武器：${w}`);
        return w;
      } catch (e) {}
    }
  }
  console.log(`[MC] 没有武器，空手攻击`);
  return null;
}

async function actionAttack(args) {
  if (!bot) return '未连接';
  await equipBestWeapon();

  const hostile = Object.values(bot.entities).filter(e => {
    if (e === bot.entity) return false;
    if (!e.position) return false;
    if (e.type === 'player') return false;
    if (!HOSTILE_MOBS.has(e.name)) return false;
    return bot.entity.position.distanceTo(e.position) <= 24;
  });

  if (hostile.length === 0) return '附近没有怪物';

  let target = hostile[0];
  let nd = bot.entity.position.distanceTo(target.position);
  for (const e of hostile) {
    const d = bot.entity.position.distanceTo(e.position);
    if (d < nd) { target = e; nd = d; }
  }

  const targetName = target.name;
  console.log(`[MC] 攻击目标：${targetName} 距离 ${nd.toFixed(1)}`);

  const movements = new Movements(bot, mcData);
  bot.pathfinder.setMovements(movements);

  const t0 = Date.now();
  try {
    while (Date.now() - t0 < 15000) {
      if (!bot.entities[target.id]) return `击杀了 ${targetName}`;
      const d = bot.entity.position.distanceTo(target.position);
      if (d > 3) {
        try {
          await bot.pathfinder.goto(new goals.GoalNear(
            target.position.x, target.position.y, target.position.z, 2
          ), 3000);
        } catch (e) {}
      } else {
        bot.attack(target);
      }
      await bot.waitForTicks(5);
    }
    return `打了 ${targetName} 15秒（还没死）`;
  } catch (e) {
    return `攻击失败: ${e.message}`;
  }
}

// ==================== 捡东西 ====================
async function actionCollect(args) {
  if (!bot) return '未连接';
  const items = Object.values(bot.entities).filter(e => {
    if (!e.position) return false;
    if (e.name !== 'item' && e.type !== 'object') return false;
    return bot.entity.position.distanceTo(e.position) <= 24;
  });
  if (items.length === 0) return '附近没有掉落物';

  const movements = new Movements(bot, mcData);
  bot.pathfinder.setMovements(movements);

  let got = 0;
  for (const it of items.slice(0, 12)) {
    if (!bot.entities[it.id]) { got++; continue; }
    try {
      await bot.pathfinder.goto(new goals.GoalNear(
        it.position.x, it.position.y, it.position.z, 1
      ), 5000);
      got++;
    } catch (e) {}
  }
  return `捡了 ${got} 个物品`;
}

// ==================== 吃东西 ====================
const FOODS = new Set([
  'bread', 'apple', 'golden_apple', 'enchanted_golden_apple',
  'cooked_beef', 'cooked_porkchop', 'cooked_chicken',
  'cooked_mutton', 'cooked_rabbit', 'cooked_salmon', 'cooked_cod',
  'carrot', 'golden_carrot', 'potato', 'baked_potato',
  'beetroot', 'beetroot_soup', 'melon_slice', 'sweet_berries',
  'glow_berries', 'cookie', 'cake', 'mushroom_stew', 'rabbit_stew',
  'pumpkin_pie', 'dried_kelp',
]);

async function actionEat(args) {
  if (!bot) return '未连接';
  if (bot.food >= 20) return '我现在不饿';
  const item = bot.inventory.items().find(i => FOODS.has(i.name));
  if (!item) return '背包里没有食物';
  try {
    await bot.equip(item, 'hand');
    await bot.consume();
    return `吃了 ${item.name}，饱食度 ${bot.food}/20`;
  } catch (e) {
    return `吃东西失败: ${e.message}`;
  }
}

// ==================== 丢弃物品（支持 数字 / all / 组） ====================
async function actionDrop(args) {
  if (!bot) return '未连接';
  const name = (args.item_name || '').trim();
  if (!name) return '没指定要丢什么';

  // 数量解析：支持 数字 / all / N组 / N组半 / 半组 / 中文数字
  let count;
  const raw = String(args.count || "1").trim().toLowerCase();

  if (raw === "all" || raw === "全部" || raw === "所有" || raw === "全丢") {
    count = 999;
  } else if (raw === "半组") {
    count = 32;
  } else {
    // 阿拉伯数字 "N组" / "N组半"
    let m = raw.match(/^(\d+)\s*组\s*(半)?$/);
    if (m) {
      count = parseInt(m[1]) * 64 + (m[2] ? 32 : 0);
    } else if (/^\d+$/.test(raw)) {
      count = Math.max(1, Math.min(999, parseInt(raw)));
    } else {
      // 中文数字 "一组" / "两组" / "三组半" / "十组"
      const cn = { '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
                   '六': 6, '七': 7, '八': 8, '九': 9, '十': 10 };
      const m2 = raw.match(/^([一二两三四五六七八九十]+)\s*组\s*(半)?$/);
      if (m2) {
        const zh = m2[1];
        let n = 0;
        if (zh === '十') n = 10;
        else if (zh.length === 1) n = cn[zh] || 1;
        else if (zh.startsWith('十')) n = 10 + (cn[zh[1]] || 0);
        else if (zh.endsWith('十')) n = (cn[zh[0]] || 1) * 10;
        else if (zh.includes('十')) {
          const parts = zh.split('十');
          n = (cn[parts[0]] || 1) * 10 + (cn[parts[1]] || 0);
        }
        count = n * 64 + (m2[2] ? 32 : 0);
      } else {
        count = 1;
      }
    }
  }

  const inv = bot.inventory.items();
  if (inv.length === 0) return '背包是空的';

  const { item, how } = findItemInInventory(inv, name);

  if (!item) {
    const summary = inv.map(i => {
      const zh = Object.entries(ITEM_NAME_MAP).find(([k, v]) => v === i.name);
      const label = zh ? zh[0] : i.name;
      return `${label}×${i.count}`;
    }).join('、');
    return `没有"${name}"。背包现有：${summary}`;
  }

  try {
    const drop_count = Math.min(count, item.count);
    await bot.toss(item.type, null, drop_count);
    console.log(`[MC] 丢弃 ${drop_count} 个 ${item.name}（匹配：${how}，请求：${count}）`);
    return `丢了 ${drop_count} 个 ${item.name}`;
  } catch (e) {
    return `丢弃失败: ${e.message}`;
  }
}

// ==================== 背包查询 ====================
function actionInventory() {
  if (!bot) return '未连接';
  const inv = bot.inventory.items();
  if (inv.length === 0) return '背包是空的';
  const lines = inv.map(i => {
    const zh = Object.entries(ITEM_NAME_MAP).find(([k, v]) => v === i.name);
    const label = zh ? zh[0] : i.name;
    return `${label}×${i.count}`;
  });
  return `背包共 ${inv.length} 种物品：${lines.join('、')}`;
}

// ==================== WebSocket 服务端 ====================
const wss = new WebSocket.Server({ port: WS_PORT });
console.log(`[WS] 服务端已启动，监听 :${WS_PORT}`);

wss.on('connection', (ws) => {
  clients.add(ws);
  console.log(`[WS] 客户端连接（当前 ${clients.size} 个）`);

  ws.on('message', async (raw) => {
    let cmd;
    try { cmd = JSON.parse(raw); } catch (e) { return; }
    const { id, action, args } = cmd;
    if (!id || !action) return;

    let ok = true, result = '';
    try {
      switch (action) {
        case 'say':       result = actionSay(args || {}); break;
        case 'follow':    result = actionFollow(args || {}); break;
        case 'come':      result = actionCome(args || {}); break;
        case 'stop':      result = actionStop(); break;
        case 'status':    result = actionStatus(); break;
        case 'look':      result = actionLook(); break;
        case 'mine':      result = await actionMine(args || {}); break;
        case 'attack':    result = await actionAttack(args || {}); break;
        case 'collect':   result = await actionCollect(args || {}); break;
        case 'eat':       result = await actionEat(args || {}); break;
        case 'drop':      result = await actionDrop(args || {}); break;
        case 'inventory': result = actionInventory(); break;
        case 'reconnect':
          if (bot) { try { bot.quit(); } catch(e){} }
          setTimeout(createBot, 500);
          result = '正在重连';
          break;
        default:
          ok = false; result = `未知指令: ${action}`;
      }
    } catch (e) {
      ok = false; result = `执行失败: ${e.message}`;
    }

    try {
      ws.send(JSON.stringify({ type: 'response', id, ok, result }));
    } catch (e) {}
  });

  ws.on('close', () => {
    clients.delete(ws);
    console.log(`[WS] 客户端断开（剩余 ${clients.size} 个）`);
  });
});

// ==================== 启动 ====================
createBot();