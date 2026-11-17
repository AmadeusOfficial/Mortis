import { type ReactNode, type SVGProps, useId } from "react";

export type IconProps = Omit<SVGProps<SVGSVGElement>, "ref"> & { size?: number };

// 统一封装基础 SVG 属性，保证所有线性图标尺寸、圆角与透传行为一致
function makeIcon(content: ReactNode) {
  return function Icon({
    size = 16,
    viewBox = "0 0 24 24",
    fill = "none",
    stroke = "currentColor",
    strokeWidth = 1.7,
    strokeLinecap = "round",
    strokeLinejoin = "round",
    "aria-hidden": ariaHidden = true,
    ...rest
  }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox={viewBox}
        fill={fill}
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap={strokeLinecap}
        strokeLinejoin={strokeLinejoin}
        aria-hidden={ariaHidden}
        {...rest}
      >
        {content}
      </svg>
    );
  };
}

const GRID_CONTENT = (
  <>
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" />
  </>
);

export const IconLibraries = makeIcon(GRID_CONTENT);

export const IconMovie = makeIcon(
  <>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" />
  </>
);

export const IconTv = makeIcon(
  <>
    <rect x="3" y="6" width="18" height="12" rx="2" />
    <path d="M8 3l4 3 4-3" />
  </>
);

export const IconAnime = makeIcon(
  <path d="m12 4 2.1 5.9L20 12l-5.9 2.1L12 20l-2.1-5.9L4 12l5.9-2.1Z" />
);

export const IconGame = makeIcon(
  <>
    <rect x="3" y="7" width="18" height="10" rx="5" />
    <path d="M8 10v4M6 12h4M15.5 11h.01M18 13h.01" />
  </>
);

export const IconUnmatched = makeIcon(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17h.01" />
  </>
);

export const IconTasks = makeIcon(
  <>
    <path d="M4 6h10M4 12h16M4 18h7" />
    <circle cx="18" cy="6" r="2" />
    <circle cx="15" cy="18" r="2" />
  </>
);

export const IconSettings = makeIcon(
  <>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" />
  </>
);

export const IconSearch = makeIcon(
  <>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </>
);

export const IconScan = makeIcon(
  <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16" />
);

export const IconScrape = makeIcon(
  <>
    <path d="M4 20 12.5 11.5" />
    <path d="m16.5 2.5 1.1 2.9 2.9 1.1-2.9 1.1-1.1 2.9-1.1-2.9-2.9-1.1 2.9-1.1Z" />
    <path d="M7 5v3M5.5 6.5h3M18.5 14v3M17 15.5h3" />
  </>
);

export const IconOrganize = makeIcon(
  <>
    <path d="M11 19H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v3" />
    <path d="M15 17h6m-2.5-2.5L21 17l-2.5 2.5" />
  </>
);

export const IconFolder = makeIcon(
  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
);

export const IconFile = makeIcon(
  <>
    <path d="M6 3h8l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" />
    <path d="M14 3v5h5" />
  </>
);

export const IconVideo = makeIcon(
  <>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="m10 9 5 3-5 3z" />
  </>
);

export const IconSubtitle = makeIcon(
  <>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="M7 15h4M13 15h4M7 11h10" />
  </>
);

export const IconImage = makeIcon(
  <>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <circle cx="8.5" cy="9" r="1.5" />
    <path d="m21 15-5-5-6 6-2-2-5 5" />
  </>
);

export const IconLock = makeIcon(
  <>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </>
);

export const IconUnlock = makeIcon(
  <>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 7.6-1.7" />
  </>
);

export const IconEdit = makeIcon(
  <>
    <path d="M4 20h4L19 9l-4-4L4 16z" />
    <path d="m13.5 6.5 4 4" />
  </>
);

export const IconMore = makeIcon(
  <>
    <circle cx="5" cy="12" r="1.2" />
    <circle cx="12" cy="12" r="1.2" />
    <circle cx="19" cy="12" r="1.2" />
  </>
);

export const IconPlay = makeIcon(<path d="M8 5l11 7-11 7z" />);

export const IconPause = makeIcon(
  <>
    <rect x="6" y="5" width="4" height="14" rx="1" />
    <rect x="14" y="5" width="4" height="14" rx="1" />
  </>
);

export const IconBack = makeIcon(<path d="m15 6-6 6 6 6" />);

export const IconChevronDown = makeIcon(<path d="m6 9 6 6 6-6" />);

export const IconChevronRight = makeIcon(<path d="m9 6 6 6-6 6" />);

export const IconPlus = makeIcon(<path d="M12 5v14M5 12h14" />);

export const IconClose = makeIcon(<path d="M18 6 6 18M6 6l12 12" />);

export const IconCheck = makeIcon(<path d="m5 12 5 5L20 7" />);

export const IconWarning = makeIcon(
  <>
    <path d="M12 3 22 20H2Z" />
    <path d="M12 9v5M12 17h.01" />
  </>
);

export const IconError = makeIcon(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="m15 9-6 6M9 9l6 6" />
  </>
);

export const IconInfo = makeIcon(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 16v-4M12 8h.01" />
  </>
);

export const IconSort = makeIcon(
  <path d="M7 4v16M3 8l4-4 4 4M17 20V4M13 16l4 4 4-4" />
);

export const IconGrid = makeIcon(GRID_CONTENT);

export const IconList = makeIcon(
  <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
);

export const IconRefresh = makeIcon(
  <path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" />
);

export const IconTrash = makeIcon(
  <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
);

export const IconExternal = makeIcon(
  <path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
);

export const IconStar = makeIcon(
  <path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
);

export const IconClock = makeIcon(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </>
);

export const IconUser = makeIcon(
  <>
    <circle cx="12" cy="8" r="4" />
    <path d="M5 20a7 7 0 0 1 14 0" />
  </>
);

export const IconPlugin = makeIcon(
  <path d="M12 22v-5M9 8V3M15 8V3M18 8v5a6 6 0 0 1-12 0V8Z" />
);

export const IconNetwork = makeIcon(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18" />
    <ellipse cx="12" cy="12" rx="4" ry="9" />
  </>
);

export const IconSun = makeIcon(
  <>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </>
);

export const IconMoon = makeIcon(
  <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
);

export const IconMenu = makeIcon(<path d="M4 6h16M4 12h16M4 18h16" />);

export const IconSidebar = makeIcon(
  <>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M9 4v16" />
  </>,
);

export const IconDrag = makeIcon(
  <>
    <circle cx="9" cy="6" r="1" />
    <circle cx="15" cy="6" r="1" />
    <circle cx="9" cy="12" r="1" />
    <circle cx="15" cy="12" r="1" />
    <circle cx="9" cy="18" r="1" />
    <circle cx="15" cy="18" r="1" />
  </>
);

export const IconLink = makeIcon(
  <>
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
  </>
);

// 莫提斯几何 Logo：底色跟随主题主色，保持简洁几何 M
export function Logo({ size = 28 }: { size?: number }) {
  const shadeId = useId();

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      role="img"
      aria-label="莫提斯"
    >
      <defs>
        <linearGradient id={shadeId} x1="2" y1="2" x2="30" y2="30" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="rgba(255, 255, 255, 0.08)" />
          <stop offset="100%" stopColor="rgba(0, 0, 0, 0.22)" />
        </linearGradient>
      </defs>
      <rect
        x="2"
        y="2"
        width="28"
        height="28"
        rx="8"
        fill="var(--mantine-primary-color-filled, #3B82F6)"
      />
      <rect x="2" y="2" width="28" height="28" rx="8" fill={`url(#${shadeId})`} />
      <rect
        x="2.5"
        y="2.5"
        width="27"
        height="27"
        rx="7.5"
        stroke="rgba(255, 255, 255, 0.22)"
      />
      <path
        d="M9 22V10.8C9 10.25 9.65 9.95 10.05 10.35L16 16.3L21.95 10.35C22.35 9.95 23 10.25 23 10.8V22"
        stroke="#FFFFFF"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="21.5" r="1.4" fill="rgba(255, 255, 255, 0.68)" />
    </svg>
  );
}

export function DataTypeIcon({ dataTypeId, ...props }: IconProps & { dataTypeId: string }) {
  switch (dataTypeId) {
    case "movie":
      return <IconMovie {...props} />;
    case "tv":
      return <IconTv {...props} />;
    case "anime":
      return <IconAnime {...props} />;
    case "galgame":
      return <IconGame {...props} />;
    default:
      return <IconLibraries {...props} />;
  }
}
