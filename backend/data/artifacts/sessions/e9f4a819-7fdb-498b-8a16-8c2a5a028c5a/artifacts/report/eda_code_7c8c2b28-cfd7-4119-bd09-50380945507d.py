import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

# Load data
df = pd.read_parquet('data.parquet')

# Function to plot scatter with R²
def plot_scatter_with_r2(x, y, title, filename):
    plt.figure(figsize=(8, 6))
    sns.scatterplot(x=x, y=y, data=df)
    model = LinearRegression().fit(df[[x]], df[y])
    y_pred = model.predict(df[[x]])
    r2 = r2_score(df[y], y_pred)
    plt.title(f'{title} (R² = {r2:.2f})')
    plt.xlabel(x)
    plt.ylabel(y)
    plt.savefig(filename)
    plt.close()

# MperR와 상관이 높은 3개의 컬럼에 대한 산점도
plot_scatter_with_r2('RM', 'MperR', 'RM vs MperR', 'plot_1.png')
plot_scatter_with_r2('LSTAT', 'MperR', 'LSTAT vs MperR', 'plot_2.png')
plot_scatter_with_r2('MEDV', 'MperR', 'MEDV vs MperR', 'plot_3.png')

# MperR 분포 분석
plt.figure(figsize=(8, 6))
sns.histplot(df['MperR'], kde=True)
plt.title('MperR Distribution')
plt.xlabel('MperR')
plt.ylabel('Frequency')
plt.savefig('plot_4.png')
plt.close()

# RM과 MperR의 상관 관계 분석
plot_scatter_with_r2('RM', 'MperR', 'RM vs MperR', 'plot_5.png')

# LSTAT과 MperR의 상관 관계 분석
plot_scatter_with_r2('LSTAT', 'MperR', 'LSTAT vs MperR', 'plot_6.png')

# MEDV와 MperR의 상관 관계 분석
plot_scatter_with_r2('MEDV', 'MperR', 'MEDV vs MperR', 'plot_7.png')