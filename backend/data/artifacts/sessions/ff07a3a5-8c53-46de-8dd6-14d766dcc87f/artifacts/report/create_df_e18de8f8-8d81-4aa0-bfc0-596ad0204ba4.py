df = pd.read_parquet('data.parquet')
df['MEDV_per_RM'] = df['MEDV'] / df['RM']
df.to_parquet('result_1.parquet', index=False)
print(f"Result shape: {df.shape}")