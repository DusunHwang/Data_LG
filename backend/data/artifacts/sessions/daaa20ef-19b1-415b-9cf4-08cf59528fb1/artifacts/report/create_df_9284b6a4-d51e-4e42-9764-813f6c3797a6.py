df = pd.read_parquet('data.parquet')
df = df[(np.abs(df['MEDV'] - df['MEDV'].mean()) <= 3 * df['MEDV'].std())]
df.to_parquet('result_1.parquet', index=False)
print(f"Result shape: {df.shape}")