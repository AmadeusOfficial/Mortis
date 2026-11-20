import styles from "./CoverCollage.module.css";

export function CoverCollage({ urls }: { urls: (string | null)[] }) {
  const cells = [0, 1, 2, 3].map((index) => urls[index] ?? null);
  return (
    <div className={styles.collage}>
      {cells.map((url, index) =>
        url !== null ? (
          <img key={index} src={url} alt="" />
        ) : (
          <div key={index} className={styles.blank} />
        ),
      )}
    </div>
  );
}
