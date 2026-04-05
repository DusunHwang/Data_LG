df = pd.read_parquet('data.parquet')
result_df = df.copy()
result_df.to_parquet('result_1.parquet', index=False)
print(f"Shape: {result_df.shape}")