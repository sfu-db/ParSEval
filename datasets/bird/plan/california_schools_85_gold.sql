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
  "id": "9",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 39,
          "name": "$39",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 5,
          "name": "$5",
          "type": "VARCHAR"
        }
      ],
      "id": "8",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 39,
                "name": "$39",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 49,
                "name": "$49",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "7",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "schools",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "VARCHAR"
                }
              ],
              "id": "6",
              "inputs": [
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
                  "limit": 2,
                  "id": "5",
                  "inputs": [
                    {
                      "relOp": "LogicalProject",
                      "project": [
                        {
                          "kind": "INPUT_REF",
                          "index": 0,
                          "name": "$0",
                          "type": "VARCHAR"
                        },
                        {
                          "kind": "INPUT_REF",
                          "index": 1,
                          "name": "$1",
                          "type": "BIGINT"
                        }
                      ],
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
                              "name": null
                            }
                          ],
                          "id": "3",
                          "inputs": [
                            {
                              "relOp": "LogicalProject",
                              "project": [
                                {
                                  "kind": "INPUT_REF",
                                  "index": 39,
                                  "name": "$39",
                                  "type": "VARCHAR"
                                }
                              ],
                              "id": "2",
                              "inputs": [
                                {
                                  "relOp": "LogicalTableScan",
                                  "table": "schools",
                                  "id": "1",
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
              ]
            }
          ]
        }
      ]
    }
  ]
}