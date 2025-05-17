{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "VARCHAR"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 10,
  "id": "7",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [
        {
          "column": 0,
          "type": "VARCHAR"
        },
        {
          "column": 1,
          "type": "VARCHAR"
        }
      ],
      "aggs": [],
      "id": "6",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 48,
              "name": "$48",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 40,
              "name": "$40",
              "type": "VARCHAR"
            }
          ],
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "IN",
                "operator": "IN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 71,
                    "name": "$71",
                    "type": "VARCHAR"
                  }
                ],
                "query": [
                  {
                    "relOp": "LogicalProject",
                    "project": [
                      {
                        "kind": "INPUT_REF",
                        "index": 3,
                        "name": "$3",
                        "type": "VARCHAR"
                      }
                    ],
                    "id": "2",
                    "inputs": [
                      {
                        "relOp": "LogicalFilter",
                        "condition": {
                          "kind": "EQUALS",
                          "operator": "=",
                          "type": "BOOLEAN",
                          "operands": [
                            {
                              "kind": "INPUT_REF",
                              "index": 1,
                              "name": "$1",
                              "type": "VARCHAR"
                            },
                            {
                              "kind": "LITERAL",
                              "value": "duel",
                              "type": "VARCHAR",
                              "nullable": false,
                              "precision": -1
                            }
                          ]
                        },
                        "variableset": "[]",
                        "id": "1",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "legalities",
                            "id": "0",
                            "inputs": []
                          }
                        ]
                      }
                    ]
                  }
                ]
              },
              "variableset": "[]",
              "id": "4",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "cards",
                  "id": "3",
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