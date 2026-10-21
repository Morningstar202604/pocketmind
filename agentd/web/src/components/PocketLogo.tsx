import { useId } from "react";

/**
 * 品牌 Logo：深色圆角底 + 青绿渐变「口袋」轮廓 + 星芒。
 *
 * 为什么重绘而不是继续用笑脸：assets/pocketmind-icon.png 与 README 里的品牌图
 * 都是「口袋 + 星芒」，而界面此前画的是一个弧形笑脸 —— 同一产品出现两套视觉，
 * 用户在小图标、PWA 图标、README 之间来回看会认不出是同一个东西。
 * 这里按官方图标 1:1 重绘为矢量，任何尺寸都清晰，且不依赖位图缩放。
 */
export function PocketLogo({ size = 34 }: { size?: number }) {
  const uid = useId().replace(/:/g, "");
  const grad = `pm-grad-${uid}`;
  const glow = `pm-glow-${uid}`;
  return (
    <svg width={size} height={size} viewBox="0 0 1024 1024" aria-hidden="true">
      <defs>
        <linearGradient id={grad} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#22c55e" />
          <stop offset="1" stopColor="#06b6d4" />
        </linearGradient>
        <radialGradient id={glow} cx="50%" cy="42%" r="52%">
          <stop offset="0" stopColor="#22d3ee" stopOpacity="0.35" />
          <stop offset="1" stopColor="#22d3ee" stopOpacity="0" />
        </radialGradient>
      </defs>
      {/* 深色底：与官方图标一致，浅色主题下也能立住 */}
      <rect width="1024" height="1024" rx="228" fill="#0f1416" />
      <rect width="1024" height="1024" rx="228" fill={`url(#${glow})`} />
      {/* 口袋轮廓：左右上沿向中心下凹成 V。线宽 72（原 54）——
          在 32~40px 的顶栏尺寸下，太细的线会糊成一个空方框（截图巡检实测）。 */}
      <path
        d="M318 414 L318 700 Q318 768 386 768 L638 768 Q706 768 706 700 L706 414 L612 414 Q560 500 512 500 Q464 500 412 414 Z"
        fill="none"
        stroke={`url(#${grad})`}
        strokeWidth="72"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {/* 星芒：四角星。中心上移到 400（口袋上沿在 414~500 的凹口处），
          并把下尖收到 470，让星芒"坐"在凹口里而不是压穿口袋线。 */}
      <path
        d="M512 252 L562 348 L658 400 L562 452 L512 548 L462 452 L366 400 L462 348 Z"
        fill="#f2feff"
      />
      <circle cx="386" cy="300" r="18" fill="#f2feff" opacity="0.9" />
      <circle cx="638" cy="300" r="18" fill="#f2feff" opacity="0.9" />
    </svg>
  );
}
