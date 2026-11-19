import classes from "./PageBackdrop.module.css";

export interface PageBackdropProps {
  url: string | null;
}

export function PageBackdrop({ url }: PageBackdropProps) {
  if (url === null) {
    return null;
  }
  return (
    <div className={classes.wrap} aria-hidden="true">
      <div
        className={classes.img}
        style={{ backgroundImage: `url("${url}")` }}
      />
      <div className={classes.scrim} />
    </div>
  );
}
