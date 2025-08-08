{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "AVG",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "INTEGER"
        }
      ],
      "type": "INTEGER",
      "name": "EXPR$0"
    }
  ],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 7,
          "name": "$7",
          "type": "INTEGER"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "LIKE",
            "operator": "LIKE",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "VARCHAR",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 1,
                    "name": "$1",
                    "type": "DATE"
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": "2012-01%",
                "type": "CHAR",
                "nullable": false,
                "precision": 8
              }
            ]
          },
          "variableset": "[]",
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "transactions_1k",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}