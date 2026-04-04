df = pd.read_parquet('data.parquet')
result_df = df[df['RAD'] == 1].copy()
result_df.to_parquet('result_1.parquet', index=False)
print(f"Result shape: {result_df.shape}")