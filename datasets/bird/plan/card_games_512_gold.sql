{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "BIGINT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [
        {
          "column": 0,
          "type": "VARCHAR"
        }
      ],
      "aggs": [
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 0,
              "type": "VARCHAR"
            }
          ],
          "type": "BIGINT",
          "name": "DESC"
        }
      ],
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 17,
              "name": "$17",
              "type": "VARCHAR"
            }
          ],
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "AND",
                "operator": "AND",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "NOT",
                    "operator": "NOT",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "IS_NULL",
                        "operator": "IS NULL",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 5,
                            "name": "$5",
                            "type": "VARCHAR"
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "NOT",
                    "operator": "NOT",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "IS_NULL",
                        "operator": "IS NULL",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 6,
                            "name": "$6",
                            "type": "VARCHAR"
                          }
                        ]
                      }
                    ]
                  }
                ]
              },
              "variableset": "[]",
              "id": "1",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "cards",
                  "id": "0",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}