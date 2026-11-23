# Demo questions (Chinese contract samples)

Answers come from `links.json` and the AdventureWorks CSVs. Amounts are USD.

1. **Pure document lookup.** Efficient Cycling 的买卖合同中，买受人收到标的物后应在几日内完成检验？质量保证期多长？
   *In the Efficient Cycling sales contract, how many days does the buyer have to inspect the goods, and how long is the warranty?*
   Answer: 10 days to inspect, and 3 days to notify a defect. The warranty is 12 months. Source: MFG-ZH-01, clauses 十、检验 2 and 十五、质量保证期. No table holds these values.

2. **Document versus table (equal).** 合同 SC-ZH-2024-057159 的合同总额与销售订单 SO57159 的金额是否一致？
   *Does the total of contract SC-ZH-2024-057159 match sales order SO57159?*
   Answer: Yes. Both are 5872.614. The contract states USD 5872.614 in 三、费用. The table value is Sales_SalesOrderHeader.SubTotal = 5872.6140 for SalesOrderID 57159. It equals the sum of the three LineTotal values.

3. **Amended term.** Efficient Cycling 合同中 HL Touring Frame - Yellow, 50（FR-T98Y-50）现在的交货期限是哪天？原来是哪天？
   *What is the current delivery date for FR-T98Y-50 in the Efficient Cycling contract, and what was it originally?*
   Answer: 25 October 2024, under amendment MFG-ZH-05 第一条, effective 1 October 2024. The original date was 11 October 2024 (MFG-ZH-01). The table still shows DueDate 2024-10-11 for SalesOrderID 57159, and the order shipped on 2024-10-06.

4. **Amended quantity and total.** 补充协议生效后，Touring-1000 Blue, 46 的数量和合同总额是多少？
   *After the amendment, what are the quantity of Touring-1000 Blue, 46 and the contract total?*
   Answer: 3 units, up from 2, at an unchanged 1430.442. The line becomes 4291.326 and the contract total becomes 7303.056. Source: MFG-ZH-05 第二条 and 第三条. Sales_SalesOrderDetail 62911 still shows OrderQty 2, and SubTotal stays 5872.614.

5. **Document versus table (deliberate difference).** 加工合同承诺的 Road-650 Red, 44 生产周期，与产品记录的制造天数相比如何？
   *How does the build time promised in the processing contract for Road-650 Red, 44 compare with the product record?*
   Answer: The contract promises at most 3 days (MFG-ZH-03 第四条). Production_Product.DaysToManufacture is 4 for ProductID 762. The contract target is one day shorter than the record.

6. **Computed from the tables.** First Department Stores 2024 年是否达到框架协议中 Mountain-200 Black, 38 的最低采购量？
   *Did First Department Stores meet its 2024 minimum take for Mountain-200 Black, 38?*
   Answer: No. The minimum is 8 units (MFG-ZH-04 第二条). Its 2024 sales orders total 5 units: 1 on SO49850, 1 on SO55278 and 3 on SO61202. The shortfall is 3 units. Under 第三条, compensation is 10% of 3 × 2294.99 = 688.497. For Women's Mountain Shorts, S the minimum is 10, and 2024 orders total 10, so that commitment is met.

7. **Computed rate versus threshold.** Vista Road Bikes 2024 年的不合格率是否超过质量协议的上限？
   *Did Vista Road Bikes exceed the defect-rate threshold in 2024?*
   Answer: Yes. The threshold is 2.00% (MFG-ZH-06 第四条: rejected ÷ received in a calendar year). Its 2024 purchase orders show 411 rejected of 18,700 received, which is 2.20% (Purchasing_PurchaseOrderDetail joined to Purchasing_PurchaseOrderHeader, VendorID 1538).

8. **Price: contract versus actual.** 框架协议中 Mountain-200 Black, 38 的协议单价是多少？2024 年 11 月实际成交价是多少？
   *What is the agreed unit price of Mountain-200 Black, 38 in the framework agreement, and what was the November 2024 order price?*
   Answer: The agreed price is 2294.99 (MFG-ZH-04 第二条), equal to Production_Product.ListPrice for ProductID 782. SO61202 (29 November 2024) has UnitPrice 1376.994. 第四条 allows order-level discounts.

9. **Lookup with a table join.** Vista Road Bikes 供应的 LL Mountain Tire 标准交货周期是多少天？是否与供应商记录一致？
   *What is the standard lead time for LL Mountain Tire from Vista Road Bikes, and does it match the vendor record?*
   Answer: 19 days (MFG-ZH-06 第一条). It equals Purchasing_ProductVendor.AverageLeadTime = 19 for ProductID 928 and BusinessEntityID 1538.

10. **Refusal.** 示例自行车制造有限公司与 Blue Lake Cycle Trading 签订的合同约定的质量保证期是多少？
    *What warranty period does the contract with Blue Lake Cycle Trading set?*
    Answer: Refuse. No document and no table names Blue Lake Cycle Trading. The answer must say the party is not in the data and must not borrow a period from another contract.
